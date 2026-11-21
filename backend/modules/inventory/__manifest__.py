{
    "name": "Inventory & Automated Perpetual Valuation",
    "version": "1.0.0",
    "category": "Inventory/Inventory",
    "summary": "Double-Entry Stock Ledger, FIFO/AVCO/Standard Costing & Automated Real-Time Accounting Entries",
    "depends": ["accounting"],
    "data": [
        "data/stock_locations.json",
        "data/product_categories.json",
    ],
    "installable": True,
    "security_rules": [
        {"model": "stock_picking", "group": "Inventory Manager", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
        {"model": "product_template", "group": "Inventory Manager", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
    ],
}
