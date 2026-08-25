# -*- coding: utf-8 -*-
{
    'name': 'AM Kalsal Database Settings',
    'version': '19.0.1.0.0',
    'summary': 'Automate initial database configurations for Inventory and MRP',
    'category': 'Inventory/Inventory',
    'author': 'Custom',
    'depends': ['stock', 'mrp','am_so_to_mrp','am_kalsal_quality','fk_kalsal_cogs','kalsal_pr_slip','fk_mixing_slip','rd_module'],
    'data': [],
    'installable': True,
    'application': False,
    'post_init_hook': 'post_init_hook',
    'license': 'LGPL-3',
}
