{
    'name': 'Rework Check Sheet',
    'version': '19.0.1.0.0',
    'summary': 'Post-Semi-Finished QC Failure Rework Tracking',
    'description': """
        Handles the reworking process for batches that failed the Semi-Finished QC.
        Links directly to the failed QC for traceability.
    """,
    'author': 'Fakhir Khan',
    'category': 'Manufacturing/Production',
    'license': 'LGPL-3',

    # CRITICAL: fk_semi_finished_qc ensures the XML inheritance/link loads correctly
    'depends': ['fk_semi_finished_qc'],

    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/rework_sheet_views.xml',
    ],
    'installable': True,
    'application': False, # Keeps the Odoo App switcher clean
}