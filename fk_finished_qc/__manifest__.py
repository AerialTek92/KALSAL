{
    'name': 'Finished Goods Quality Check',
    'version': '19.0.1.0.0',
    'summary': 'Post-Production Microbiological QC for Finished Products (FG Test Report)',
    'description': """
        Final quality inspection of finished goods (FG Test Report KPL-FS-PR-11-FM-19).
        Parameters are pulled from the product's Finished Goods QC Parameters tab (fg_specs).
        Only Sale Orders whose Semi-Finished QC has passed are eligible.
        Failed FG batches are held/discarded — no rework loop.
    """,
    'author': 'Fakhir Khan',
    'category': 'Manufacturing/Production',
    'license': 'LGPL-3',
    'depends': ['am_kalsal_quality','fk_semi_finished_qc', 'fk_fg_reporting'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/finished_qc_views.xml',
    ],
    'installable': True,
    'application': False,
}