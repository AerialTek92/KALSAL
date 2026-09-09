from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    purchase_order_id = fields.Many2one('purchase.order', string='Purchase Order')
    x_budget_analytic_id = fields.Many2one('custom.budget', string='Forecast Budget')
    mo_ids = fields.Many2many(
        comodel_name='mrp.production',
        compute='_compute_same_so_mos',
        string='Sibling Orders from Same SO'
    )
    sibling_mo_count = fields.Integer(
        string="Sibling MO Count",
        compute="_compute_sibling_mo_count"
    )

    @api.depends('origin')
    def _compute_sibling_mo_count(self):
        for mo in self:
            if mo.origin:
                # Search for all sibling MOs sharing the same SO origin string
                # We count ALL related MOs (including this one) for the list view context
                mo.sibling_mo_count = self.search_count([('origin', '=', mo.origin)])
            else:
                mo.sibling_mo_count = 0


    @api.depends('origin')
    def _compute_same_so_mos(self):
        for mo in self:
            # If there is no tracking source origin (e.g. "SO001"), leave it empty
            if not mo.origin:
                mo.mo_ids = [(5, 0, 0)]  # Clears relation line mapping
                continue

            # 2. Search for all MOs sharing the exact same text tracking string
            # and exclude the active record ID so it doesn't duplicate itself in the grid
            sibling_mos = self.search([
                ('origin', '=', mo.origin),
                ('id', '!=', mo.id)
            ])

            # 3. Pass the found recordsets straight to the computed field proxy
            mo.mo_ids = sibling_mos

    def action_view_sibling_mos(self):
        """ Triggered by the XML Smart Button to show all matching orders without default filters """
        self.ensure_one()

        # 1. Fetch the action configuration (Notice the [0] at the end to get the dictionary)
        action_data = self.env.ref('mrp.mrp_production_action').read()[0]

        # 2. Make a clean copy of the isolated dictionary data
        action = dict(action_data)

        # 3. Filter strictly by the origin string (shows all 5 MOs regardless of state)
        action['domain'] = [('origin', '=', self.origin)]

        # 4. Clear out standard system filter tags
        action['context'] = {
            'search_default_todo': False,
            'search_default_filter_to_do': False,
            'default_origin': self.origin
        }

        return action

    def action_view_analytic_account(self):
        """
        Phase 1 Approval Link:
        Opens the Master Forecast Budget from the Manufacturing Order
        """
        self.ensure_one()
        if not self.x_budget_analytic_id:
            raise UserError(_("No budget has been generated for this order yet."))

        return {
            'name': _('Master Forecast Budget'),
            'type': 'ir.actions.act_window',
            'res_model': 'custom.budget',
            'view_mode': 'form',
            'res_id': self.x_budget_analytic_id.id,
            'target': 'current',
        }

    def action_generate_bulk_budget(self):
        """
        1. Processes all selected MOs
        2. Calculates Shortness & Old Rates
        3. Redirects user to the Master Budget Form
        """
        if not self:
            return

        for rec in self:
            if rec.state == 'draft':
                raise UserError(
                    "One of selected records doesn't contain the Recipe required for manufacturing. Kindly List Down the BOM for that product and confirm the record manually.")
            if rec.state == 'confirmed':
                # action_confirm already handles writing qty_producing and generating budget
                rec.action_confirm()
            else:
                # Just refresh the budget for current state
                rec._generate_forecast_budget()

        # --- AUTO-REDIRECT TO BUDGET ---
        target_budget = self[0].x_budget_analytic_id

        if target_budget:
            return {
                'name': _('Master Forecast Budget'),
                'type': 'ir.actions.act_window',
                'res_model': 'custom.budget',
                'view_mode': 'form',
                'res_id': target_budget.id,
                'target': 'current',
            }

    def action_custom_save(self):
        """
        Triggered by the custom Save button.
        Note: Odoo's UI automatically writes pending changes to the database
        before executing this method. We just return True to stay on the form.
        """
        return True

    def action_assign(self):
        pass

    def action_assign_lots_to_mo_lines(self):
        StockQuant = self.env['stock.quant']
        StockMoveLine = self.env['stock.move.line']

        def _mo_priority_key(mo):
            if 'date_start' in mo._fields and mo.date_start:
                return mo.date_start
            if 'date_planned_start' in mo._fields and mo.date_planned_start:
                return mo.date_planned_start
            return mo.create_date

        mos_fifo = self.sorted(key=_mo_priority_key)

        for mo in mos_fifo:

            raw_moves = mo.move_raw_ids.filtered(
                lambda m:
                m.product_id.tracking in ('lot', 'serial')
                and m.state not in ('done', 'cancel')
            )

            raw_moves = raw_moves.sorted(key=lambda m: m.create_date)

            for move in raw_moves:

                product = move.product_id
                source_location = move.location_id

                # Quantity already assigned to this move
                already_on_this_move = sum(
                    move.move_line_ids.mapped('quantity')
                )

                qty_still_needed = (
                        move.product_uom_qty - already_on_this_move
                )

                if qty_still_needed <= 0:
                    continue

                # ONLY look at positive stock with a LOT
                quants = StockQuant.search([
                    ('product_id', '=', product.id),
                    ('location_id', '=', source_location.id),
                    ('quantity', '>', 0),
                    ('lot_id', '!=', False),
                ], order='in_date asc, id asc')

                new_move_line_vals = []

                for quant in quants:

                    if qty_still_needed <= 0:
                        break

                    lot = quant.lot_id

                    # Quantity of this lot already consumed/reserved
                    # by other open MO moves.
                    consumed_elsewhere = sum(
                        StockMoveLine.search([
                            ('lot_id', '=', lot.id),
                            ('move_id', '!=', move.id),
                            ('move_id.state', 'not in', ('done', 'cancel')),
                        ]).mapped('quantity')
                    )

                    remaining_in_lot = (
                            quant.quantity - consumed_elsewhere
                    )

                    if remaining_in_lot <= 0:
                        continue

                    assign_qty = min(
                        qty_still_needed,
                        remaining_in_lot
                    )

                    existing_line = move.move_line_ids.filtered(
                        lambda l: l.lot_id.id == lot.id
                    )

                    if existing_line:
                        existing_line[0].quantity += assign_qty

                    else:
                        new_move_line_vals.append(
                            (0, 0, {
                                'move_id': move.id,
                                'product_id': product.id,
                                'product_uom_id': move.product_uom.id,
                                'location_id': move.location_id.id,
                                'location_dest_id': move.location_dest_id.id,
                                'lot_id': lot.id,
                                'quantity': assign_qty,
                            })
                        )

                    qty_still_needed -= assign_qty

                # IMPORTANT:
                # Do NOT call _action_assign().
                # That would invoke Odoo's native reservation logic
                # and can create a lot-less line for the remaining demand.

                if new_move_line_vals:
                    move.write({
                        'move_line_ids': new_move_line_vals
                    })

                # If nothing is available, don't create anything.
                # The move should simply remain partially/unavailable.


    def _generate_forecast_budget(self):
        """
        Phase 1 Consolidated Logic:
        Merges shared components across all MOs for the same Sales Order.
        Rate is the single most recent purchase price per component.
        """
        so_mapping = {}
        for rec in self:
            so_name = rec.origin.split(' ')[0] if rec.origin else False
            if so_name and so_name not in so_mapping:
                so_mapping[so_name] = rec

        for so_name, rec in so_mapping.items():
            sale_order = self.env['sale.order'].search([('name', '=', so_name)], limit=1)
            if not sale_order:
                continue

            budget = sale_order.x_budget_id or self.env['custom.budget'].create({
                'name': _("Master Budget: %s") % sale_order.name,
                'partner_id': sale_order.partner_id.id,
                'origin': sale_order.id,
                'date_from': fields.Date.today(),
            })

            related_mos_in_self = self.filtered(lambda m: m.origin and m.origin.startswith(so_name))
            related_mos_in_self.write({'x_budget_analytic_id': budget.id})
            sale_order.x_budget_id = budget.id

            all_related_mos = self.env['mrp.production'].search([('origin', 'ilike', so_name)])

            consolidated_products = {}
            summary_line_vals = []

            for mo in all_related_mos:
                this_mo_total_cost = 0.0

                for move in mo.move_raw_ids:
                    shortness = move.x_items_short
                    if shortness <= 0:
                        continue

                    item_to_budget = shortness

                    pid = move.product_id.id
                    if pid not in consolidated_products:
                        rate = self._get_last_purchase_rate(pid) or move.product_id.standard_price
                        consolidated_products[pid] = {'qty': 0.0, 'rate': rate, 'fg_ids': set()}

                    consolidated_products[pid]['qty'] += item_to_budget
                    consolidated_products[pid]['fg_ids'].add(mo.product_id.id)
                    this_mo_total_cost += (consolidated_products[pid]['rate'] * item_to_budget)

                if this_mo_total_cost > 0:
                    summary_line_vals.append((0, 0, {
                        'finished_good_id': mo.product_id.id,
                        'mo_id': mo.id,
                        'total_budget_amount': this_mo_total_cost,
                    }))

            product_line_vals = []
            for prod_id, data in consolidated_products.items():
                product_line_vals.append((0, 0, {
                    'product_id': prod_id,
                    'finished_good_id': [(6, 0, list(data['fg_ids']))],
                    'qty': data['qty'],
                    'old_rate': data['rate'],
                }))

            budget.write({
                'product_line_ids': [(5, 0, 0)] + product_line_vals,
                'budget_line_ids': [(5, 0, 0)] + summary_line_vals,
            })

    def _get_last_purchase_rate(self, product_id):
        """
        Returns the price_unit of the most recently confirmed/done purchase
        order line for this product, using the PO's own approval/order date
        to determine "most recent" — sorted in Python since the ORM can't
        order by a related field through a Many2one in a search() call.
        """
        po_lines = self.env['purchase.order.line'].search([
            ('product_id', '=', product_id),
            ('state', 'in', ['purchase', 'done']),
        ])

        if not po_lines:
            return 0.0

        most_recent = max(
            po_lines,
            key=lambda l: l.order_id.date_approve or l.order_id.date_order or l.create_date
        )
        return most_recent.price_unit

    # This field shows the child shortages in the main MO list
    x_shortness_summary = fields.Char(string='Shortage Details', compute='_compute_shortness_summary')

    def _compute_shortness_summary(self):
        for rec in self:
            short_items = rec.move_raw_ids.filtered(lambda l: l.x_items_short > 0)

            if short_items:
                display_list = [f"{l.product_id.name} ({l.x_items_short}{l.product_uom.name})" for l in short_items[:2]]

                if len(short_items) > 2:
                    remaining = len(short_items) - 2
                    rec.x_shortness_summary = f"{', '.join(display_list)} ... (+{remaining} more)"
                else:
                    rec.x_shortness_summary = ", ".join(display_list)
            else:
                rec.x_shortness_summary = "✓ All Stock Available"

    # --- Triggers ---

    def action_confirm(self):
        for rec in self:
            products = rec.move_raw_ids.mapped('product_id')
            if products:
                self.env.cr.execute(
                    "SELECT id FROM product_product WHERE id IN %s FOR UPDATE",
                    (tuple(products.ids),)
                )
        res = super(MrpProduction, self).action_confirm()
        for rec in self:
            if rec.qty_producing != rec.product_qty:
                rec.write({'qty_producing': rec.product_qty})
            rec.do_unreserve()
        self.action_assign_lots_to_mo_lines()
        self._generate_forecast_budget()
        return res

    def write(self, vals):
        if any(f in vals for f in ['move_raw_ids', 'product_qty', 'qty_producing']):
            products = self.move_raw_ids.mapped('product_id')
            if products:
                self.env.cr.execute(
                    "SELECT id FROM product_product WHERE id IN %s FOR UPDATE",
                    (tuple(products.ids),)
                )
        res = super(MrpProduction, self).write(vals)

        if any(f in vals for f in ['move_raw_ids', 'product_qty', 'qty_producing']):
            self._generate_forecast_budget()

            if not self.env.context.get('skip_stock_enforcement'):
                for mo in self.with_context(skip_stock_enforcement=True):
                    tracked_moves = mo.move_raw_ids.filtered(
                        lambda m: m.product_id.tracking in ('lot', 'serial') and m.state not in ('done', 'cancel')
                    )
                    untracked_moves = mo.move_raw_ids - tracked_moves

                    tracked_moves._clear_unbacked_consumption()
                    mo.action_assign_lots_to_mo_lines()
                    untracked_moves._cap_consumed_to_available_stock()

        return res


    @api.model_create_multi
    def create(self, vals_list):
        productions = super(MrpProduction, self).create(vals_list)
        # x_items_short is a computed field, it calculates automatically when accessed.
        return productions


class StockMove(models.Model):
    _inherit = 'stock.move'
    # Removed store=True so it changes "time to time" with stock/demand
    x_items_short = fields.Float(string='Shortness', compute='_compute_shortness')

    def _is_relevant(self, move):
        """
        A raw-material move counts toward shortness only if:
          - it has a product and belongs to an active Manufacturing Order
          - that MO isn't already done/cancelled
          - it's tied to a Sales Order that is actually confirmed ('sale' state)
        """
        if not (move.product_id and move.raw_material_production_id and move.state not in ('done', 'cancel')):
            return False

        mo = move.raw_material_production_id
        if mo.state == 'done':
            return False

        # Defensive SO lookup — don't assume reference_ids exists on this build.
        so = self.env['sale.order']

        if 'reference_ids' in mo._fields:
            so = mo.reference_ids.sale_ids

        if not so and mo.sale_line_id:
            so = mo.sale_line_id.order_id

        if not so and hasattr(mo, 'procurement_group_id') and mo.procurement_group_id.sale_id:
            so = mo.procurement_group_id.sale_id

        return bool(so) and so[:1].state == 'sale'

    def _compute_shortness(self):
        stock_location = self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        production_location = self.env.ref('stock.location_production', raise_if_not_found=False)

        relevant_moves = self.filtered(lambda m: self._is_relevant(m))
        (self - relevant_moves).x_items_short = 0.0

        products = relevant_moves.mapped('product_id')

        for product in products:
            all_demand_moves = self.env['stock.move'].search([
                ('product_id', '=', product.id),
                ('raw_material_production_id', '!=', False),
                ('state', 'not in', ('done', 'cancel')),
            ]).filtered(lambda m: self._is_relevant(m))

            # FIFO priority key, resilient to field-name differences across versions.
            def _mo_priority_key(move):
                mo = move.raw_material_production_id
                if 'date_start' in mo._fields and mo.date_start:
                    return mo.date_start
                if 'date_planned_start' in mo._fields and mo.date_planned_start:
                    return mo.date_planned_start
                return mo.create_date

            ordered_moves = all_demand_moves.sorted(key=_mo_priority_key)

            on_hand = 0.0
            if stock_location:
                on_hand += product.with_context(location=stock_location.id).qty_available
            if production_location:
                on_hand += product.with_context(location=production_location.id).qty_available

            available_pool = on_hand
            shortness_by_move = {}

            for move in ordered_moves:
                consumed = sum(move.move_line_ids.mapped('quantity'))
                remaining_demand = max(0.0, move.product_uom_qty - consumed)

                covered = min(available_pool, remaining_demand)
                available_pool -= covered

                shortness_by_move[move.id] = max(0.0, remaining_demand - covered)

            for move in relevant_moves.filtered(lambda m: m.product_id == product):
                move.x_items_short = shortness_by_move.get(move.id, 0.0)

    def _clear_unbacked_consumption(self):
        """Removes any auto-generated move line that has no lot, and zeroes
        out lines whose lot has no actual available quantity."""
        for move in self:
            for line in move.move_line_ids:
                if not line.lot_id:
                    line.unlink()
                    continue
                available_in_lot = self.env['stock.quant']._get_available_quantity(
                    move.product_id, move.location_id, lot_id=line.lot_id
                )
                if available_in_lot <= 0:
                    line.unlink()
                elif line.quantity > available_in_lot:
                    line.quantity = available_in_lot

    def _cap_consumed_to_available_stock(self):
        """For non-tracked products: never let Consumed exceed real on-hand
        at the move's source location."""
        for move in self:
            available_qty = self.env['stock.quant']._get_available_quantity(
                move.product_id, move.location_id
            )
            total_set = sum(move.move_line_ids.mapped('quantity'))
            if available_qty <= 0:
                move.move_line_ids.write({'quantity': 0.0})
            elif total_set > available_qty:
                factor = available_qty / total_set
                for line in move.move_line_ids:
                    line.quantity = line.quantity * factor