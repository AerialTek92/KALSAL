# -*- coding: utf-8 -*-
from odoo import api


def post_init_hook(env):
    """
    Odoo 19 post_init_hook logic configuring Warehouse, Multi-Step routes,
    and explicitly activating the 'Unlock Manufacturing Orders' configuration checkbox.
    """
    # 1. Enable Global App Settings
    config = env['res.config.settings'].create({
        'group_stock_multi_locations': True,  # Activates Storage Locations
        'group_stock_adv_location': True,  # Activates Multi-Step Routes Checkbox
        'group_stock_production_lot': True,  # Activates Lots and Serial Numbers
        'group_mrp_byproducts': True,  # Activates By-Products
        'group_mrp_routings': True,  # Activates Advanced Work Orders / Routings
        'group_unlocked_by_default': True,  # ENABLES THE 'UNLOCK MANUFACTURING ORDERS' CHECKBOX
    })
    config.execute()

    # 2. Force Access Groups Refresh for internal users to show multi-step menus immediately
    try:
        multi_route_group = env.ref('stock.group_adv_location')
        if multi_route_group:
            internal_users = env['res.users'].search([
                ('share', '=', False),
                ('active', '=', True)
            ])
            multi_route_group.write({'users': [(4, user.id) for user in internal_users]})
    except Exception:
        pass

    # 3. Identify the Active Default Warehouse
    warehouse = env['stock.warehouse'].search([], limit=1)
    if not warehouse:
        return

    # Configure physical warehouse setting to process receipts in 2 Steps
    warehouse.write({
        'reception_steps': 'two_steps',
    })

    parent_location = warehouse.view_location_id

    # 4. Handle WH/Quality Location Creation
    quality_loc = env['stock.location'].search([
        ('name', '=', 'Quality'),
        ('location_id', '=', parent_location.id)
    ], limit=1)
    if not quality_loc:
        quality_loc = env['stock.location'].create({
            'name': 'Quality',
            'complete_name': f'{warehouse.code}/Quality',
            'location_id': parent_location.id,
            'usage': 'internal',
        })

    # 5. Handle WH/Production Location Creation
    prod_loc = env['stock.location'].search([
        ('name', '=', 'Production'),
        ('location_id', '=', parent_location.id)
    ], limit=1)

    if not prod_loc:
        prod_loc = env['stock.location'].create({
            'name': 'Production',
            'complete_name': f'{warehouse.code}/Production',
            'location_id': parent_location.id,
            'usage': 'production',
        })
    else:
        prod_loc.write({'usage': 'production'})

    # 6. Map Operation Types Default Locations
    if warehouse.in_type_id:
        warehouse.in_type_id.write({
            'default_location_dest_id': quality_loc.id
        })

    if warehouse.manu_type_id:
        warehouse.manu_type_id.write({
            'default_location_src_id': prod_loc.id,
            'default_location_dest_id': prod_loc.id
        })

    # 7. Adjust Rules inside Routes
    receipt_route = env['stock.route'].search([
        ('warehouse_ids', 'in', warehouse.ids),
        ('name', 'like', 'Receive')
    ], limit=1)

    if receipt_route:
        for rule in receipt_route.rule_ids:
            if rule.location_dest_id == warehouse.lot_stock_id:
                rule.write({
                    'location_src_id': quality_loc.id
                })

    mrp_route = env['stock.route'].search([
        ('warehouse_ids', 'in', warehouse.ids),
        ('name', 'like', 'Manufacture')
    ], limit=1)

    if mrp_route:
        for rule in mrp_route.rule_ids:
            rule.write({
                'location_src_id': prod_loc.id,
                'location_dest_id': warehouse.lot_stock_id.id
            })

    # 8. Unarchive and Enable Replenish on Order (MTO) Route
    mto_route = env['stock.route'].search([
        ('name', 'like', 'Replenish on Order'),
        ('active', '=', False)
    ], limit=1)
    if mto_route:
        mto_route.write({'active': True})
