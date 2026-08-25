from odoo import models, fields, api, _
from odoo.exceptions import UserError


class CogsSalesAnalysis(models.Model):
    _name = 'cogs.sales.analysis'
    _description = 'Sales COGS Analysis'
    _order = 'create_date desc'

    name = fields.Char(string='Reference', required=True, readonly=True, default='New')
    sale_order_id = fields.Many2one('sale.order', string='Sales Order', required=True, readonly=True)
    partner_id = fields.Many2one(related='sale_order_id.partner_id', string='Customer', store=True)
    budget_id = fields.Many2one('custom.budget', string='Linked Forecast Budget', readonly=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('computed', 'Computed'),
        ('locked', 'Locked')
    ], string='Status', default='draft', tracking=True)

    # Summary Totals
    total_forecasted_cost = fields.Float(string='Total Forecasted Cost', compute='_compute_totals', store=True)
    total_actual_cost = fields.Float(string='Total Actual Cost', compute='_compute_totals', store=True)
    total_variance = fields.Float(string='Total Variance', compute='_compute_totals', store=True)

    # Hierarchy Lines
    fg_line_ids = fields.One2many('cogs.fg.line', 'analysis_id', string='Finished Product Lines')

    # NEW: Display PO Numbers
    purchase_order_ids = fields.Many2many(
        'purchase.order',
        compute='_compute_purchase_order_ids',
        string='Purchase Orders'
    )
    purchase_order_count = fields.Integer(
        compute='_compute_purchase_order_ids',
        string='PO Count'
    )
    po_names_display = fields.Char(
        compute='_compute_purchase_order_ids',
        string='PO References',
        help="List of Purchase Orders used in this COGS calculation"
    )

    def _compute_purchase_order_ids(self):
        """Compute the POs linked to this COGS via the Budget -> Requisition"""
        for rec in self:
            if rec.budget_id and rec.budget_id.requisition_id:
                pos = rec.budget_id.requisition_id.purchase_order_ids.filtered(
                    lambda p: p.state != 'cancel'
                )
                rec.purchase_order_ids = pos.ids
                rec.purchase_order_count = len(pos)
                rec.po_names_display = ', '.join(pos.mapped('name')) if pos else ''
            else:
                rec.purchase_order_ids = False
                rec.purchase_order_count = 0
                rec.po_names_display = ''

    def action_view_purchase_orders(self):
        """Open the Purchase Orders linked to this COGS Analysis"""
        self.ensure_one()

        if not self.purchase_order_ids:
            return {'type': 'ir.actions.act_window_close'}

        return {
            'name': _('Purchase Orders'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.purchase_order_ids.ids)],
            'context': {'create': False},
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('cogs.sales.analysis') or 'New'
        return super().create(vals_list)

    @api.depends('fg_line_ids.forecasted_total', 'fg_line_ids.actual_total')
    def _compute_totals(self):
        for rec in self:
            rec.total_forecasted_cost = sum(rec.fg_line_ids.mapped('forecasted_total'))
            rec.total_actual_cost = sum(rec.fg_line_ids.mapped('actual_total'))
            rec.total_variance = rec.total_forecasted_cost - rec.total_actual_cost

    def action_compute_cogs(self):
        """Calculates Forecasted vs Actual costs allocated per Finished Good."""
        self.ensure_one()
        if self.state == 'locked':
            raise UserError(_("This analysis is locked and cannot be recomputed."))

        budget = self.sale_order_id.x_budget_id
        if not budget:
            raise UserError(_("No Master Forecast Budget found for this Sales Order. Please generate it first."))

        # ==========================================
        # NEW STRICT VALIDATION: GRN + QUALITY GATE
        # ==========================================
        requisition = budget.requisition_id
        if not requisition:
            raise UserError(_("No Purchase Requisition has been generated for this budget yet."))

        # Find active POs linked to this budget (exclude cancelled ones)
        active_pos = requisition.purchase_order_ids.filtered(lambda p: p.state != 'cancel')

        incomplete_pos = []

        # Map your custom picking states to readable text for the error message
        state_labels = {
            'draft': 'Pending Receipt',
            'waiting': 'Pending Receipt',
            'assigned': 'Ready for Receipt',
            'vehicle_inspection': 'Vehicle Inspection',
            'inspection_failed': 'Vehicle Failed Inspection',
            'qc_pending': 'Waiting for QC',
            'qc_failed': 'QC Failed'
        }

        for po in active_pos:
            # Get incoming pickings for this PO
            incoming_pickings = po.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming')

            # A PO is only complete if it has pickings AND all of them are in the 'done' state.
            # In your custom flow, 'done' is only reached AFTER passing Vehicle Inspection, GRN, and QC.
            not_done_pickings = incoming_pickings.filtered(lambda p: p.state != 'done')

            if not incoming_pickings or not_done_pickings:
                # Build a readable list of why it's blocked
                issues = []
                for p in not_done_pickings:
                    label = state_labels.get(p.state, p.state.replace('_', ' ').title())
                    issues.append(f"{p.name} ({label})")

                incomplete_pos.append({
                    'po_name': po.name,
                    'issues': issues
                })

        if incomplete_pos:
            error_msg = _(
                "Cannot generate COGS yet! The following Purchase Orders have pending GRN or Quality Checks:\n\n")
            for item in incomplete_pos:
                error_msg += f"• {item['po_name']}: {', '.join(item['issues'])}\n"

            error_msg += _(
                "\nPlease ensure all raw materials are fully received and have successfully passed Quality Control.")
            raise UserError(error_msg)
        # ==========================================

        # 1. Clear existing lines
        self.fg_line_ids = [(5, 0, 0)]

        # 2. Aggregate ACTUAL purchase data for this budget (Including Taxes)
        actual_costs = {}
        po_lines = self.env['purchase.order.line'].search([
            ('order_id.pr_order_id.budget_id', '=', budget.id),
            ('order_id.state', 'in', ['purchase', 'done'])
        ])
        for pol in po_lines:
            pid = pol.product_id.id
            if pid not in actual_costs:
                actual_costs[pid] = {'qty': 0.0, 'total_cost': 0.0}
            actual_costs[pid]['qty'] += pol.product_qty

            # FIX: Use pol.price_total instead of (qty * price_unit) to include taxes
            actual_costs[pid]['total_cost'] += pol.price_total

        # 3. Get all MOs for this Sales Order
        so_name = self.sale_order_id.name.split(' ')[0]
        mos = self.env['mrp.production'].search([('origin', 'ilike', so_name)])

        # NEW: Pre-calculate total required qty per product across all MOs for accurate allocation
        total_required_per_product = {}
        for mo in mos:
            for move in mo.move_raw_ids:
                pid = move.product_id.id
                total_required_per_product[pid] = total_required_per_product.get(pid, 0.0) + move.product_uom_qty

        fg_lines_vals = []

        # 4. Process each Finished Good (MO)
        for mo in mos:
            rm_lines_vals = []
            fg_forecasted_total = 0.0
            fg_actual_total = 0.0

            for move in mo.move_raw_ids:
                rm_product = move.product_id
                required_qty = move.product_uom_qty

                # --- FORECASTED COST (Aligned with Master Budget Shortness) ---
                budget_line = budget.product_line_ids.filtered(lambda l: l.product_id.id == rm_product.id)

                # Initialize allocated qty to 0
                allocated_budget_qty = 0.0

                if budget_line:
                    forecasted_unit_price = budget_line[0].old_rate
                    # Allocate the budgeted shortness qty proportionally to this MO
                    total_req = total_required_per_product.get(rm_product.id, required_qty)
                    allocated_budget_qty = budget_line[0].qty * (required_qty / total_req) if total_req > 0 else 0.0
                    forecasted_total = allocated_budget_qty * forecasted_unit_price
                else:
                    # If no shortness was budgeted (fully in stock), forecasted purchase cost is 0
                    forecasted_unit_price = 0.0
                    forecasted_total = 0.0

                fg_forecasted_total += forecasted_total

                # --- ACTUAL COST (Weighted Average Unit Price) ---
                actual_unit_price = 0.0
                actual_total = 0.0

                if rm_product.id in actual_costs and actual_costs[rm_product.id]['qty'] > 0:
                    # Calculate weighted average unit price from actual POs
                    actual_unit_price = actual_costs[rm_product.id]['total_cost'] / actual_costs[rm_product.id]['qty']

                    # FIX: Use allocated_budget_qty (the amount actually purchased/short)
                    # instead of required_qty (total MO need)
                    actual_total = allocated_budget_qty * actual_unit_price

                fg_actual_total += actual_total

                rm_lines_vals.append((0, 0, {
                    'raw_material_id': rm_product.id,
                    'required_qty': required_qty,
                    'forecasted_unit_price': forecasted_unit_price,
                    'forecasted_total': forecasted_total,
                    'actual_unit_price': actual_unit_price,
                    'actual_total': actual_total,
                    'variance': forecasted_total - actual_total,
                }))

            fg_lines_vals.append((0, 0, {
                'finished_good_id': mo.product_id.id,
                'mo_id': mo.id,
                'forecasted_total': fg_forecasted_total,
                'actual_total': fg_actual_total,
                'variance': fg_forecasted_total - fg_actual_total,
                'rm_line_ids': rm_lines_vals,
            }))

        # 5. Write the computed hierarchy
        self.write({
            'fg_line_ids': fg_lines_vals,
            'budget_id': budget.id,
            'state': 'computed'
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_lock(self):
        self.write({'state': 'locked'})


class CogsFgLine(models.Model):
    _name = 'cogs.fg.line'
    _description = 'COGS Finished Good Line'

    analysis_id = fields.Many2one('cogs.sales.analysis', string='Analysis', ondelete='cascade')
    finished_good_id = fields.Many2one('product.product', string='Finished Product')
    mo_id = fields.Many2one('mrp.production', string='Source Manufacturing Order')

    forecasted_total = fields.Float(string='Forecasted Cost')
    actual_total = fields.Float(string='Actual Cost')
    variance = fields.Float(string='Variance')

    rm_line_ids = fields.One2many('cogs.rm.line', 'fg_line_id', string='Raw Materials Used')


class CogsRmLine(models.Model):
    _name = 'cogs.rm.line'
    _description = 'COGS Raw Material Line'

    fg_line_id = fields.Many2one('cogs.fg.line', string='Finished Good Line', ondelete='cascade')
    raw_material_id = fields.Many2one('product.product', string='Raw Material')
    required_qty = fields.Float(string='Qty Required')

    forecasted_unit_price = fields.Float(string='Forecasted Unit Price')
    forecasted_total = fields.Float(string='Forecasted Total')

    actual_unit_price = fields.Float(string='Actual Unit Price')
    actual_total = fields.Float(string='Actual Total')

    variance = fields.Float(string='Variance')