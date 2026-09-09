{
    'name': 'Unbound Incoming PO Tracker',
    'version': '19.0.1.0.0',
    'summary': 'Shows pending POs that are not bound to any demand on shortness displays',
    'description': """
        Extends the Manufacturing shortness and Budget forecasting views to show
        incoming stock from pending Purchase Orders that are NOT promised to any
        specific demand (unbound stock). Helps planners understand real exposure.
    """,
    'author': 'Fakhir Khan',
    'category': 'Manufacturing/Production',
    'license': 'LGPL-3',
    'depends': ['am_so_to_mrp', 'stock', 'purchase'],
    'data': [
        'views/stock_move_views.xml',
        'views/budget_views.xml',
    ],
    'installable': True,
    'application': False,
}