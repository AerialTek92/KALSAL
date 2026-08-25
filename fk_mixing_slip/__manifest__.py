{
    'name': 'Mixing Slip',
    'version': '19.0.1.0.0',
    'summary': 'Mixing / Production reconciliation slip linked to MRS',
    'author': 'Fakhir Khan',
    'license': 'LGPL-3',
    'depends': ['kalsal_pr_slip', 'mrp'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/mixing_slip_views.xml',
        'views/material_requisition_slip_views.xml',
    ],
    'installable': True,
    'application': False,
}
