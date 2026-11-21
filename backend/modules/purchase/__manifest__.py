{
    "name": "Purchase Management (Procure-to-Pay)",
    "version": "1.0.0",
    "category": "Inventory/Purchase",
    "summary": "RFQs, Purchase Orders, Incoming Shipments, Vendor Bills & 3-Way Matching Control",
    "depends": ["accounting", "inventory"],
    "data": [],
    "installable": True,
    "security_rules": [
        {"model": "purchase_order", "group": "Inventory Manager", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
        {"model": "purchase_order", "group": "Accountant", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
    ],
}
