# -*- coding: utf-8 -*-
{
    'name': 'AM Kalsal Database Settings',
    'version': '19.0.1.0.0',
    'summary': 'Automate initial database configurations for Inventory and MRP',
    'category': 'Inventory/Inventory',
    'author': 'Custom',
    'depends': [
        # Core Odoo apps
        'stock',
        'mrp',
        'account',
        'base_automation',

        # Main production/QC chain (each depends on the one before it):
        # PR/MRS -> Mixing & Wastage -> Quality (Vehicle/Lvl 1) -> Semi-Finished QC
        # -> forks into Finished QC / Rework / FG Reporting
        'kalsal_pr_slip',
        'fk_mixing_slip',
        'am_kalsal_quality',
        'fk_semi_finished_qc',
        'fk_finished_qc',
        'fk_kalsal_rework_slip',
        'fk_fg_reporting',

        # Sales -> Budget -> COGS chain
        'am_so_to_mrp',
        'fk_kalsal_cogs',

        # Standalone utilities (nothing else depends on these)
        'rd_module',
        'am_kalsal_teco',
        'web_listview_column_width_cr',
    ],
    'data': [],
    'installable': True,
    'application': False,
    'post_init_hook': 'post_init_hook',
    'license': 'LGPL-3',
}