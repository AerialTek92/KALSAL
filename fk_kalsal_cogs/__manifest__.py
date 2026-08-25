{
    'name': 'Kalsal COGS Analysis',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Track Forecasted vs Actual COGS per Finished Product',
    'description': """
        Analyzes Cost of Goods Sold by comparing the Master Forecast Budget 
        against actual Purchase Order costs, allocated proportionally to each 
        Finished Product in the Sales Order.
    """,
    'author': 'Fakhir Khan',
    'depends': ['base', 'sale', 'mrp', 'purchase', 'am_so_to_mrp', 'account', 'accountant'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/cogs_analysis_views.xml',
        # 'views/sale_order_inherit.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
