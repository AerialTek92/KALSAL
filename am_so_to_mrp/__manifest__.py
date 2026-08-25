{
    'name': "Sale Order to MRP",

    'summary': "",

    'description': """
Long description of module's purpose
    """,

    'author': "Alimohammed",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'demo',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'sale', 'mrp', 'account',
                'account_budget', 'purchase', 'sale'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'views/purchase_requisition_views.xml',
        'reports/purchase_order_report.xml',
        'data/ir_sequence_data.xml',
        'views/custom_budget_view.xml',
        'views/sales_order.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
}
