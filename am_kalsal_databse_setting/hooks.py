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

    # 9. Create the '610000 Consumable Expenses' Chart of Account
    # NOTE: 'base_automation' and 'account' are now hard dependencies in the
    # manifest, so both modules are guaranteed to already be installed by the
    # time this hook runs - no manual module activation needed here.
    expense_account = env['account.account'].search([
        ('code', '=', '610000')
    ], limit=1)
    if not expense_account:
        expense_account = env['account.account'].create({
            'code': '610000',
            'name': 'Consumable Expenses',
            'account_type': 'expense',
            'reconcile': False,
        })

    # 10. Create the 'QC Sample Consumption' Location (Inventory Loss type)
    qc_sample_loc = env['stock.location'].search([
        ('name', '=', 'QC Sample Consumption'),
        ('usage', '=', 'inventory')
    ], limit=1)
    if not qc_sample_loc:
        loc_vals = {
            'name': 'QC Sample Consumption',
            'usage': 'inventory',  # Displayed in UI as "Inventory Loss"
        }
        # 'Loss Account' is a core Odoo 19 field on Inventory Loss locations
        # (in 18 and earlier this was split into valuation_in/out_account_id;
        # 19 merged it into one field). Detect the exact name dynamically
        # rather than hardcoding it, since it varies slightly by version.
        for candidate_field in (
            'valuation_out_account_id',
            'loss_account_id',
            'valuation_in_account_id',
        ):
            if candidate_field in env['stock.location']._fields:
                loc_vals[candidate_field] = expense_account.id
                break
        qc_sample_loc = env['stock.location'].create(loc_vals)

    # 11. Create the 'Auto-consume 200g on GRN Validation' Automation Rule
    picking_model = env['ir.model']._get('stock.picking')

    state_field = env['ir.model.fields'].search([
        ('model', '=', 'stock.picking'),
        ('name', '=', 'state')
    ], limit=1)

    done_selection = env['ir.model.fields.selection'].search([
        ('field_id', '=', state_field.id),
        ('value', '=', 'done')
    ], limit=1)

    automation_rule = env['base.automation'].search([
        ('name', '=', 'Auto-consume 200g on GRN Validation')
    ], limit=1)

    if not automation_rule and state_field and done_selection:
        automation_rule = env['base.automation'].create({
            'name': 'Auto-consume 200g on GRN Validation',
            'model_id': picking_model.id,
            'trigger': 'on_state_set',
            'trg_selection_field_id': done_selection.id,
        })

    if automation_rule:
        server_action = env['ir.actions.server'].search([
            ('base_automation_id', '=', automation_rule.id)
        ], limit=1)

        automation_code = """
if record.picking_type_id.code == 'incoming' and 'Quality' in record.location_dest_id.complete_name:
    dest_loc = env['stock.location'].search([
        ('name', '=', 'QC Sample Consumption'),
        ('usage', '=', 'inventory')
    ], limit=1)
    if dest_loc:
        for move in record.move_ids:
            if move.quantity == 0:
                continue
            new_lot = False
            target_location_id = move.location_dest_id.id
            for line in move.lot_ids:
                valid = line.product_uom_id.name == 'kg' and line.product_qty > 1

                if not valid:
                    break
                
                line.write({
                    'ref': f'{record.name}',
                    'location_id': move.location_dest_id.id,
                    'check_location_id': move.location_dest_id.id
                })
                new_lot = line.id
                # new_lot = env["stock.lot"].create({
                #     'name': f'S/{line.name}',
                #     'product_id': move.product_id.id,
                #     'company_id': record.company_id.id,
                #     'location_id': record.location_dest_id.id
                # })
                # raise UserError(line.location_id)
                
            scrap_record = env['stock.scrap'].create({
                'product_id': move.product_id.id,
                'product_uom_id': move.product_uom.id,
                'scrap_qty': 0.200,
                'location_id': target_location_id, 
                'scrap_location_id': dest_loc.id,
                'lot_id': new_lot,
                'picking_id': record.id,
                'company_id': record.company_id.id,
            })
            scrap_record.action_validate()
"""

        if not server_action:
            env['ir.actions.server'].create({
                'name': 'Execute Code',
                'base_automation_id': automation_rule.id,
                'model_id': picking_model.id,
                'state': 'code',
                'code': automation_code,
            })