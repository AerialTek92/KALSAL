{
    'name': 'TECO For Karabi Production',
    'version': '1.0',
    'summary': 'TECO Production Form for Karabi',
    'description': "TECO For Karabi Production Form",
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'category': 'Manufacturing',
    'depends': ['sale_management', 'mail', 'mrp'],
    'data': [
        'security/ir.model.access.csv',  # <-- ADD THIS LINE
        'data/sequence_data.xml',
        'views/teco_views.xml',
    ],
    'license': 'LGPL-3',
    'application': False,
    'installable': True,
}