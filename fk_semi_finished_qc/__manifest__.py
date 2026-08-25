{
    'name': 'Semi Finished Quality Check',
    'version': '19.0.1.0.0',
    'summary': 'Post-Mixing Quality Checks for Recipe Mixes',
    'description': """
        Handles the quality inspection of semi-finished goods after the mixing process. 
        Links directly to the Mixing Slip and reuses global quality parameters.
    """,
    'author': 'Fakhir Khan',
    'category': 'Manufacturing/Production',
    'license': 'LGPL-3',

    # CRITICAL: fk_mixing_slip is declared here so the XML inheritance
    # ref="fk_mixing_slip.view_mixing_slip_form" loads correctly without crashing.
    # am_kalsal_quality is declared to guarantee kalsal.quality.parameter is accessible.
    'depends': ['fk_mixing_slip', 'am_kalsal_quality'],

    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/semi_finished_qc_views.xml',
    ],
    'installable': True,
    'application': False,  # Keeps the Odoo App switcher clean, just like fk_kalsal_cogs
}