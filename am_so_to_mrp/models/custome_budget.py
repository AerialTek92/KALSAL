from dateutil.relativedelta import relativedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError


# ==========================================
# CUSTOM BUDGET MODELS
# ==========================================

class CustomBudget(models.Model):
    _name = 'custom.budget'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Custom MO Forecast Budget'
    _order = 'name desc'

    name = fields.Char(string='Budget Reference', required=True, tracking=True)

    requisition_id = fields.Many2one('purchase.requisition.form', string='PR', readonly=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('waiting', 'Waiting For Approval'),
        ('approved', 'Approved'),
        ('cancel', 'Cancelled')
    ], string='Status', default='draft', tracking=True)

    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    user_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user)
    origin = fields.Many2one('sale.order',string='Order Number')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)

    mo_ids = fields.One2many('mrp.production', 'x_budget_analytic_id', string='Mo Ids')

    mo_count = fields.Integer(string='PO Count', compute='_compute_mo_count')


    date_from = fields.Date(string='Start Date', default=fields.Date.today)
    date_to = fields.Date(string='End Date')

    product_line_ids = fields.One2many('budget.product.line', 'budget_id', string='Product Lines')
    budget_line_ids = fields.One2many('budget.summary.line', 'budget_id', string='Budget Summary')

    def _compute_mo_count(self):
        for rec in self:
            rec.mo_count = len(rec.mo_ids)

    def action_custom_save(self):
        """
        Triggered by the custom Save button.
        Note: Odoo's UI automatically writes pending changes to the database
        before executing this method. We just return True to stay on the form.
        """
        return True


    def action_view_manufacturing_orders(self):
        """
        Phase 1 Traceability:
        Opens the list of MOs that contributed to this Master Budget.
        """
        self.ensure_one()

        # 1. Define the action targeting mrp.production
        action = {
            'name': _('Manufacturing Orders'),
            'type': 'ir.actions.act_window',
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            # FIX: Use the field that exists on mrp.production (x_budget_analytic_id)
            'domain': [('x_budget_analytic_id', '=', self.id)],
            'context': {'default_x_budget_analytic_id': self.id},
        }

        # 2. Optimization: Find all MOs linked to this budget
        # We search mrp.production for any record pointing to this budget ID
        linked_mos = self.env['mrp.production'].search([('x_budget_analytic_id', '=', self.id)])

        if len(linked_mos) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': linked_mos.id,
            })

        return action



    def action_open_requisition(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Purchase Requisition',
            'res_model': 'purchase.requisition.form',
            'res_id': self.requisition_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_confirm(self):
        self.write({'state': 'waiting'})

    def action_approve(self):
        """ Phase 1 Approval: Converts Shortness into Draft Purchase Requisitions and opens it """
        self.ensure_one()

        # 1. Permission and state security verification checks
        if not self.env.user.has_group('am_so_to_mrp.group_budget_approver'):
            raise UserError(
                _("You do not have the required permissions to approve this budget. Please contact your manager."))

        if self.requisition_id:
            raise UserError(_('A Purchase Requisition has already been created for this budget.'))

        if not self.product_line_ids:
            raise UserError(_('Cannot approve a budget with no product lines.'))

        # 2. Create the Purchase Requisition Header
        requisition_vals = {
            'requisition_type': 'raw',  # Defaulting fallback type
            'originator_name': self.user_id.name,
            'order_number': self.origin.id,
            'budget_id': self.id,
            'state': 'draft',
        }

        requisition = self.env['purchase.requisition.form'].create(requisition_vals)

        # 3. Build Requisition Lines and fetch historical PO prices for each product
        requisition_lines = []
        quotation_lines = []
        for line in self.product_line_ids:
            if not line.product_id:
                continue

            # STEP A: Find all approved POs first, ordered by approval date (Newest First)
            # This avoids the strict Odoo 19 dot-walking limitation in the order parameter
            approved_pos = self.env['purchase.order'].search([
                ('state', 'in', ['purchase', 'done']),
                ('date_approve', '!=', False)
            ], order='date_approve desc')  # Perfectly valid because date_approve belongs to purchase.order

            # STEP B: Find the 3 latest PO lines for this product matching those approved POs
            po_lines = self.env['purchase.order.line'].search([
                ('product_id', '=', line.product_id.id),
                ('order_id', 'in', approved_pos.ids)
            ], limit=3)

            # Re-sort po_lines manually by the order's date_approve to guarantee correct chronology
            # (Since the 'in' operator doesn't preserve database order in Odoo 19)
            sorted_po_lines = sorted(
                po_lines,
                key=lambda pol: pol.order_id.date_approve or fields.Datetime.now(),
                reverse=True
            )

            # Initialize pricing fields
            first_price, first_vendor = 0.0, False
            second_price, second_vendor = 0.0, False
            third_price, third_vendor = 0.0, False

            # Assign prices according to historical hierarchy index matching
            for index, po_line in enumerate(sorted_po_lines):
                if index == 0:
                    first_price = po_line.price_unit
                    first_vendor = po_line.order_id.partner_id.id
                elif index == 1:
                    second_price = po_line.price_unit
                    second_vendor = po_line.order_id.partner_id.id
                elif index == 2:
                    third_price = po_line.price_unit
                    third_vendor = po_line.order_id.partner_id.id

            # Prepare the command record tuple list payload
            requisition_lines.append((0, 0, {
                'product_id': line.product_id.id,
                'quantity_required': line.qty,
                'finished_good_id': line.finished_good_id.ids,
                'first_last_price': first_price if first_price else '-',
                'first_last_vendor_id': first_vendor,
                'second_last_price': second_price if second_price else '-',
                'second_last_vendor_id': second_vendor,
                'third_last_price': third_price if third_price else '-',
                'third_last_vendor_id': third_vendor,
            }))
            quotation_lines.append((0, 0, {
                'product_id': line.product_id.id,
                'quantity_required': line.qty,
                'finished_good_id': line.finished_good_id.ids,
            }))

        # Write lines to the requisition record
        requisition.write({'purchase_line_ids': requisition_lines,'quotation_line_ids':quotation_lines})

        # Update current budget status and tracking reference
        self.write({
            'state': 'approved',
            'requisition_id': requisition.id
        })

        # 4. Return an action window to immediately redirect the user to the new Requisition Form
        return {
            'name': _('Purchase Requisition Form'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.requisition.form',
            'res_id': requisition.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_cancel(self):
        self.write({'state': 'cancel'})


class BudgetProductLine(models.Model):
    _name = 'budget.product.line'
    _description = 'Itemized Shortness Details'

    budget_id = fields.Many2one('custom.budget', ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    qty = fields.Float(string='Shortness Qty')
    old_rate = fields.Float(string='Last Rate')
    subtotal = fields.Float(string='Subtotal', compute='_compute_subtotal', store=True)
    mo_id = fields.Many2one('mrp.production', string='Source MO')
    finished_good_id = fields.Many2many('product.product', string='Finished Good')

    @api.depends('qty', 'old_rate')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.qty * line.old_rate


class BudgetSummaryLine(models.Model):
    _name = 'budget.summary.line'
    _description = 'Finished Good Summary'
    budget_id = fields.Many2one('custom.budget', ondelete='cascade', index=True)
    finished_good_id = fields.Many2one('product.product', string='Finished Good')
    mo_id = fields.Many2one('mrp.production', string='Source MO')
    total_budget_amount = fields.Float(string='Production Cost')