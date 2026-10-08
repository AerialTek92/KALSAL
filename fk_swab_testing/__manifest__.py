{
    'name': 'Swab Testing Report',
    'version': '19.0.1.0.0',
    'summary': 'Microbiological Swab Testing Report (Machine / Environment Swabs)',
    'description': """
        Swab Testing Report (KPL-FS-PR-11-FM-19 style).
        Simple fill-and-save document with per-sample microbiological results.
    """,
    'author': 'Fakhir Khan',
    'category': 'Manufacturing/Production',
    'license': 'LGPL-3',
    'depends': ['am_kalsal_quality'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/swab_testing_views.xml',
        'reports/swab_report.xml',
    ],
    'installable': True,
    'application': False,
}
