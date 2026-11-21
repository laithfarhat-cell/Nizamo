{
    "name": "Sales Management (Order-to-Cash)",
    "version": "1.0.0",
    "category": "Sales/Sales",
    "summary": "Quotations, Sales Orders, Auto-Delivery Generation & Customer Invoicing",
    "depends": ["accounting", "inventory"],
    "data": [],
    "installable": True,
    "security_rules": [
        {"model": "sale_order", "group": "Sales User", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
        {"model": "sale_order", "group": "Billing Officer", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
    ],
}
