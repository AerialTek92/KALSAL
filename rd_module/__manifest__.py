{
    'name': 'R&D',
    'version': '1.0',
    'category': 'Productivity',
    'summary': 'Research and Development Module',
    'description': 'Custom module for R and D activities - Bills of Materials management',
    'author': 'Fakhir Khan',
    # 👇 THIS IS THE CRITICAL FIX 👇
    'depends': ['base', 'product', 'mrp', 'sale'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'views/hide_menus.xml',
        'views/rd_menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
