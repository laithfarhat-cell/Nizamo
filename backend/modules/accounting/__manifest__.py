{
    "name": "Double-Entry Accounting & Financial Reporting",
    "version": "1.0.0",
    "category": "Accounting/Accounting",
    "summary": "IFRS/GAAP Double-Entry General Ledger, Invoicing, Tax Engine, Multi-Currency FX Reconciliation & Financial Statements",
    "depends": [],
    "data": [
        "data/chart_of_accounts.json",
        "data/journals.json",
        "data/taxes.json",
    ],
    "installable": True,
    "security_rules": [
        {"model": "account_move", "group": "Accountant", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
        {"model": "account_move", "group": "Billing Officer", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
        {"model": "account_payment", "group": "Accountant", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
        {"model": "account_account", "group": "Accountant", "perm_read": True, "perm_write": True, "perm_create": True, "perm_unlink": False},
    ],
}
