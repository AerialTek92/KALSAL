{
    'name': 'Finished Goods Reporting',
    'version': '19.0.1.0.0',
    'summary': 'Finished Goods Production Reporting (Cartons / Boxes vs SO)',
    'description': """
        Finished Goods Reporting document inside Manufacturing.
        Select a Sale Order (only SOs whose Semi-Finished QC has passed) and the
        system auto-builds one line per eligible product with S#, Product Name
        and Lot #. The production team then enters cartons/boxes to-be-produced
        vs produced, with a mandatory reason for any short/excess quantity.
    """,
    'author': 'Fakhir Khan',
    'category': 'Manufacturing/Production',
    'license': 'LGPL-3',
    'depends': [
        'sale',
        'mrp',
        'stock',
        'fk_semi_finished_qc',  # <--- THIS IS CRITICAL
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/fg_reporting_views.xml',
    ],
    'installable': True,
    'application': False,
}
