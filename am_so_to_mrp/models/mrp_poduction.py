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

    def _generate_forecast_budget(self):
        """
        Phase 1 Consolidated Logic:
        Merges shared components across all MOs for the same Sales Order.
        """
        # Group MOs by Sales Order to avoid redundant budget recalculations
        # if multiple MOs for the same SO are processed simultaneously.
        so_mapping = {}
        for rec in self:
            so_name = rec.origin.split(' ')[0] if rec.origin else False
            if so_name and so_name not in so_mapping:
                so_mapping[so_name] = rec

        for so_name, rec in so_mapping.items():
            sale_order = self.env['sale.order'].search([('name', '=', so_name)], limit=1)
            if not sale_order:
                continue

            # 1. Get or Create Master Budget
            budget = sale_order.x_budget_id or self.env['custom.budget'].create({
                'name': _("Master Budget: %s") % sale_order.name,
                'partner_id': sale_order.partner_id.id,
                'origin': sale_order.id,
                'date_from': fields.Date.today(),
            })

            # Link all MOs in current context to this budget
            related_mos_in_self = self.filtered(lambda m: m.origin and m.origin.startswith(so_name))
            related_mos_in_self.write({'x_budget_analytic_id': budget.id})
            sale_order.x_budget_id = budget.id

            # 2. Aggregate Data from ALL MOs for this Sales Order
            all_related_mos = self.env['mrp.production'].search([('origin', 'ilike', so_name)])

            # Dictionary to track {product_id: {'qty': total_qty, 'rate': rate, 'fg_ids': set()}}
            consolidated_products = {}
            summary_line_vals = []

            for mo in all_related_mos:
                this_mo_total_cost = 0.0

                for move in mo.move_raw_ids:
                    # Accessing x_items_short triggers the compute automatically
                    shortness = move.x_items_short
                    if shortness > 0:
                        item_to_budget = min(shortness, move.product_uom_qty)

                        pid = move.product_id.id
                        if pid not in consolidated_products:
                            po_line = self.env['purchase.order.line'].search([
                                ('product_id', '=', pid), ('state', 'in', ['purchase', 'done'])
                            ], order='create_date desc', limit=1)
                            rate = po_line.price_unit if po_line else move.product_id.standard_price
                            consolidated_products[pid] = {'qty': 0.0, 'rate': rate, 'fg_ids': set()}

                        consolidated_products[pid]['qty'] += item_to_budget
                        # Track all finished goods that require this raw material
                        consolidated_products[pid]['fg_ids'].add(mo.product_id.id)
                        this_mo_total_cost += (consolidated_products[pid]['rate'] * item_to_budget)

                # Add to Summary Tab (Keep this granular per Finished Good)
                if this_mo_total_cost > 0:
                    summary_line_vals.append((0, 0, {
                        'finished_good_id': mo.product_id.id,  # This is Many2one, so .id is correct
                        'mo_id': mo.id,
                        'total_budget_amount': this_mo_total_cost,
                    }))

            # 3. Format Consolidated Lines for Odoo Write
            product_line_vals = []
            for prod_id, data in consolidated_products.items():
                product_line_vals.append((0, 0, {
                    'product_id': prod_id,
                    'finished_good_id': [(6, 0, list(data['fg_ids']))],  # Proper Many2many command
                    'qty': data['qty'],
                    'old_rate': data['rate'],
                }))

            # 4. Clear and Overwrite the Budget with Consolidated Data
            budget.write({
                'product_line_ids': [(5, 0, 0)] + product_line_vals,
                'budget_line_ids': [(5, 0, 0)] + summary_line_vals,
            })
        # ==========================================
        # NEW: FIFO LOT RESERVATION LOGIC
        # ==========================================

    def _assign_fifo_lots(self):
        """
        Automatically reserves specific lots based on FIFO (First-In, First-Out)
        for products tracked by lot/serial numbers.
        """
        StockQuant = self.env['stock.quant']
        for mo in self:
            for move in mo.move_raw_ids:
                # Only apply to moves that need reservation and are tracked by lots
                if move.product_id.tracking in ('lot', 'serial') and move.state in ('confirmed', 'assigned',
                                                                                    'partially_available'):

                    # Unreserve any default reservations first to strictly apply our FIFO logic
                    move._do_unreserve()

                    # Find available quants in the source location, sorted by FIFO (in_date ascending)
                    quants = StockQuant.search([
                        ('product_id', '=', move.product_id.id),
                        ('location_id', '=', move.location_id.id),
                        ('quantity', '>', 0),
                        ('lot_id', '!=', False)
                    ], order='in_date asc, id asc')

                    qty_to_reserve = move.product_uom_qty
                    move_lines_vals = []

                    for quant in quants:
                        if qty_to_reserve <= 0:
                            break

                        # Calculate available quantity on this quant (Total - Already Reserved)
                        available_qty = quant.quantity - quant.reserved_quantity
                        if available_qty <= 0:
                            continue

                        # Reserve the minimum of what we need vs what is available in this lot
                        reserve_qty = min(qty_to_reserve, available_qty)

                        move_lines_vals.append((0, 0, {
                            'move_id': move.id,
                            'product_id': move.product_id.id,
                            'product_uom_id': move.product_uom.id,
                            'location_id': move.location_id.id,
                            'location_dest_id': move.location_dest_id.id,
                            'lot_id': quant.lot_id.id,
                            'quantity': reserve_qty,
                            'quantity_product_uom': reserve_qty,
                        }))

                        qty_to_reserve -= reserve_qty

                    # Write the move lines to formally reserve the stock
                    if move_lines_vals:
                        move.write({'move_line_ids': move_lines_vals})
                        move._action_assign()  # Re-evaluate move state to 'assigned'

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
        res = super(MrpProduction, self).action_confirm()
        for rec in self:
            if rec.qty_producing != rec.product_qty:
                rec.write({'qty_producing': rec.product_qty})
        # Write override will handle _generate_forecast_budget if qty changed.
        # If not changed, we trigger manually to ensure budget is created on confirm.
        # if self.unreserve_visible:
        #     self.do_unreserve()
        self._generate_forecast_budget()

        # Trigger the custom FIFO reservation after confirmation
        self._assign_fifo_lots()


        return res

    def write(self, vals):
        res = super(MrpProduction, self).write(vals)
        if any(f in vals for f in ['move_raw_ids', 'product_qty', 'qty_producing']):
            self._generate_forecast_budget()
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

    def _compute_shortness(self):
        # Fetch both WH/Stock and WH/Production locations
        stock_location = self.env.ref('stock.stock_location_stock', raise_if_not_found=False)
        production_location = self.env.ref('stock.location_production', raise_if_not_found=False)

        for move in self:
            if move.product_id:
                on_hand = 0.0

                # Check stock in WH/Stock
                if stock_location:
                    on_hand += move.product_id.with_context(location=stock_location.id).qty_available

                # Check stock in WH/Production
                if production_location:
                    on_hand += move.product_id.with_context(location=production_location.id).qty_available

                move.x_items_short = max(0, move.product_uom_qty - on_hand)
            else:
                move.x_items_short = 0
