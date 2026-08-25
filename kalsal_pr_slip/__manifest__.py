{
    'name': 'Material Requisition Slip',
    'version': '1.0.0',
    'summary': 'Material Requisition Slip / Lot Making with SO & BOM integration',
    'description': """
Material Requisition Slip (MRS) Module
======================================
- User selects a Sale Order first.
- When a Recipe (product) is selected:
    * UOM is auto-filled.
    * Quantity is auto-filled from the SO line.
    * BOM components of the selected product are auto-populated as separate lines.
- Supports confirm / done / cancel workflow.
""",
    'author': 'Alimohammed Wadiwala',
    'category': 'Manufacturing/Production',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'mrp', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/material_requisition_slip_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
}