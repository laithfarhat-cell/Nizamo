"""Odoo-inspired Modular ERP Plugin Registry with topological dependency resolution."""

from __future__ import annotations

import ast
import importlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, FastAPI


@dataclass
class ModuleManifest:
    """Parsed metadata from a module's `__manifest__.py`."""

    technical_name: str
    name: str
    version: str
    depends: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    installable: bool = True
    summary: str = ""
    category: str = "Uncategorized"
    security_rules: list[dict[str, Any]] = field(default_factory=list)
    path: Path = field(default_factory=Path)


class ModuleRegistry:
    """Discovers ERP modules, resolves topological dependencies, and registers routes/models/security."""

    def __init__(self, modules_root: Path | None = None) -> None:
        self.modules_root = modules_root or (Path(__file__).resolve().parent.parent / "modules")
        self.manifests: dict[str, ModuleManifest] = {}
        self.load_order: list[str] = []
        self.routers: list[tuple[str, APIRouter]] = []
        self.security_rules: list[dict[str, Any]] = []
        self.seed_hooks: list[Callable[..., Any]] = []
        self._loaded = False

    def discover(self) -> dict[str, ModuleManifest]:
        """Scan `backend/modules/*/__manifest__.py` and parse manifest declarations."""
        self.manifests.clear()
        if not self.modules_root.exists():
            return self.manifests

        for child in sorted(self.modules_root.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            manifest_file = child / "__manifest__.py"
            if not manifest_file.exists():
                continue

            raw = ast.literal_eval(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid __manifest__.py in {child.name}: expected dict")

            manifest = ModuleManifest(
                technical_name=child.name,
                name=str(raw.get("name", child.name)),
                version=str(raw.get("version", "1.0.0")),
                depends=list(raw.get("depends", [])),
                data=list(raw.get("data", [])),
                installable=bool(raw.get("installable", True)),
                summary=str(raw.get("summary", "")),
                category=str(raw.get("category", "General")),
                security_rules=list(raw.get("security_rules", [])),
                path=child,
            )
            if manifest.installable:
                self.manifests[child.name] = manifest

        return self.manifests

    def resolve_dependency_order(self) -> list[str]:
        """Compute topological load order (Kahn's algorithm) validating missing & cyclic dependencies."""
        if not self.manifests:
            self.discover()

        in_degree: dict[str, int] = {name: 0 for name in self.manifests}
        dependents: dict[str, list[str]] = {name: [] for name in self.manifests}

        for mod_name, manifest in self.manifests.items():
            for dep in manifest.depends:
                if dep not in self.manifests:
                    raise RuntimeError(
                        f"Module '{mod_name}' depends on missing or non-installable module '{dep}'"
                    )
                dependents[dep].append(mod_name)
                in_degree[mod_name] += 1

        queue: deque[str] = deque(sorted(name for name, deg in in_degree.items() if deg == 0))
        order: list[str] = []

        while queue:
            current = queue.popleft()
            order.append(current)
            for nxt in sorted(dependents[current]):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(self.manifests):
            remaining = sorted(set(self.manifests.keys()) - set(order))
            raise RuntimeError(f"Circular module dependency detected among: {remaining}")

        self.load_order = order
        return order

    def load_all(self) -> list[str]:
        """Import models, routers, security rules, and seed hooks in topological order."""
        if self._loaded:
            return self.load_order

        order = self.resolve_dependency_order()
        self.routers.clear()
        self.security_rules.clear()
        self.seed_hooks.clear()

        for mod_name in order:
            manifest = self.manifests[mod_name]
            base_pkg = f"backend.modules.{mod_name}"

            # 1. Import models so SQLAlchemy Base.metadata registers all tables
            models_file = manifest.path / "models.py"
            if models_file.exists():
                importlib.import_module(f"{base_pkg}.models")

            # 2. Import package root (optional seed hooks or custom registrations)
            pkg = importlib.import_module(base_pkg)
            if hasattr(pkg, "register_seed_data") and callable(pkg.register_seed_data):
                self.seed_hooks.append(pkg.register_seed_data)

            # 3. Import FastAPI router if defined
            router_file = manifest.path / "router.py"
            if router_file.exists():
                router_mod = importlib.import_module(f"{base_pkg}.router")
                router = getattr(router_mod, "router", None)
                if isinstance(router, APIRouter):
                    self.routers.append((mod_name, router))

            # 4. Collect declared security rules
            for rule in manifest.security_rules:
                self.security_rules.append({"module": mod_name, **rule})

        self._loaded = True
        return order

    def mount_routers(self, app: FastAPI, prefix: str = "/api/v1") -> None:
        """Register all discovered module routers onto the FastAPI app."""
        if not self._loaded:
            self.load_all()
        for _mod_name, router in self.routers:
            app.include_router(router, prefix=prefix)

    def describe_modules(self) -> list[dict[str, Any]]:
        """Serialize installed module registry metadata for API inspection."""
        if not self._loaded:
            self.load_all()
        return [
            {
                "technical_name": name,
                "name": self.manifests[name].name,
                "version": self.manifests[name].version,
                "depends": self.manifests[name].depends,
                "data": self.manifests[name].data,
                "installable": self.manifests[name].installable,
                "summary": self.manifests[name].summary,
                "category": self.manifests[name].category,
                "load_rank": idx + 1,
            }
            for idx, name in enumerate(self.load_order)
        ]


registry = ModuleRegistry()
