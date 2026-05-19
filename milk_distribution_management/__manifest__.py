{
    'name': 'Milk Distribution Management',
    'version': '19.0.14.1.0',
    'category': 'Industries',
    'summary': 'Complete milk distribution ERP — dispatch, ledger, crates, dairy purchase, adjustments, settlements',
    'author': 'Giriraj Enterprise',
    'depends': [
        'base',
        'mail',
        'account',
        'product',
        'stock',
        'accountant',
        'purchase',
        'sale_management',
        'contacts',
    ],
    'data': [
        # ── Security (groups FIRST, then access rules) ───────────
        'security/groups.xml',
        'security/ir.model.access.csv',

        # ── Sequences + Cron ────────────────────────────────────
        'data/sequence.xml',
        'data/cron.xml',

        # ── Seed data (noupdate) ─────────────────────────────────
        'data/route_data.xml',

        # ── Wizards ─────────────────────────────────────────────
        'views/dispatch_import_wizard_views.xml',

        # ── Views ───────────────────────────────────────────────
        'views/route_views.xml',
        'views/product_crate_views.xml',
        'views/dealer_fields_views.xml',
        'views/dispatch_views.xml',
        'views/dealer_default_order_views.xml',
        'views/ledger_views.xml',
        'views/cash_views.xml',
        'views/quick_cash_wizard_views.xml',
        'views/daily_summary_views.xml',
        'views/crate_views.xml',
        'views/crate_billing_views.xml',
        'views/dairy_purchase_views.xml',
        'views/dairy_order_views.xml',
        'views/delivery_adjustment_views.xml',
        'views/bank_settlement_views.xml',
        'views/dairy_ledger_views.xml',
        'views/outstanding_report_views.xml',
        'views/dealer_statement_views.xml',
        'views/product_sales_views.xml',
        'views/driver_performance_views.xml',
        'views/monthly_closing_views.xml',
        'views/periodic_report_views.xml',
        'views/payment_mode_report_views.xml',
        'views/pl_summary_views.xml',
        'views/menu.xml',

        # ── Reports ─────────────────────────────────────────────
        'reports/bill_report.xml',
        'reports/driver_sheet.xml',
        'reports/dairy_purchase_bill.xml',
        'reports/outstanding_report.xml',
        'reports/dealer_statement.xml',
        'reports/payment_receipt.xml',
        'reports/product_sales_report.xml',
        'reports/monthly_closing_report.xml',

        # ── Periodic Reports (Monthly/Quarterly/Yearly) ─────────────────────
        'reports/periodic_reports.xml',
        'reports/payment_mode_report.xml',
        'reports/pl_summary_report.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
