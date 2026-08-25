{
    'name': 'Vehicle Inspection',
    'version': '19.0.1.0.0',
    'summary': 'Manage Vehicle Inspections and Checklists',
    'description': """
        This application allows you to manage vehicle inspections.
        You can track tires, brakes, lights, engine oil, and overall
        vehicle conditions.
    """,
    'author': 'Alimohammed',
    'category': 'Services/Fleet',
    'depends': ['stock', 'purchase','purchase_stock', 'fleet', 'mail','fk_mixing_slip'],
    'data': [
        'security/ir.model.access.csv',
        'security/security_group.xml',
        'data/sequence.xml',
        'data/ir_cron_data.xml',
        'views/vehicle_inspection.xml',
        'views/line_clearance_view.xml',
        'views/return_picking.xml',
        'views/custom_quality_check.xml',
        'views/global_test_parameter_view.xml',
        'views/stock_picking_view.xml'
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}