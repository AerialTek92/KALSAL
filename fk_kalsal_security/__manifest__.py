{
    'name': 'KalSal Security & Access Control',
    'version': '19.0.1.0.0',
    'category': 'Extra Tools',
    'author': 'Fakhir Khan',
    'summary': 'Centralized User Rights & Access Control for KalSal Operations',
    'description': """
        Centralized security module for KalSal.
        Defines a unified 'KalSal Operations' category with hierarchical groups:
        - Executors (Procurement, Stores, Production, Finance)
        - Management (R&D, Sales, Quality, Finance Approvers)
        - Factory Administrator & CEO
    """,
    'author': 'KalSal IT Team',
    'depends': [
        'base',
        'stock',
        'purchase',
        'mrp',
        'sale',
        'account',
        'am_so_to_mrp',
        'rd_module',
        'am_kalsal_quality',
        'kalsal_pr_slip',
        'fk_mixing_slip',
        'fk_semi_finished_qc',
        'fk_finished_qc',
        'fk_kalsal_rework_slip',
        'fk_fg_reporting',
        'am_kalsal_teco',
        'fk_kalsal_cogs',
    ],
    'data': [
        'security/security_groups.xml',
        'security/ir.model.access.csv',
        'views/purchase_order_views.xml',
        'views/custom_save_views.xml',
        'views/grn_access_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
