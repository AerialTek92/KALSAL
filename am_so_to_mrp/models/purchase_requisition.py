from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PurchaseRequisition(models.Model):
    _name = 'purchase.requisition.form'
    _description = 'Purchase Requisition Form'
    _order = 'id desc'

    name = fields.Char(string='Seq No', required=True, readonly=True, default='New')
    date = fields.Date(string='Date', default=fields.Date.today, required=True)
    purchase_order_ids = fields.One2many('purchase.order', 'pr_order_id', string='Generated Purchase Orders')
    requisition_type = fields.Selection([
        ('raw', 'Raw Material'),
        ('packaging', 'Packaging Material'),
        ('indirect', 'Indirect Material'),
        ('general', 'General Material'),
        ('trading', 'Trading Material'),
    ], string='Requisition Type', required=True)
    originator_name = fields.Char(string='Originator Name')
    material_used_in = fields.Char(string='Material Used In')
    order_number = fields.Many2one('sale.order', string='Order Number')
    party_name = fields.Char(string='Party Name')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('waiting', 'Waiting For Approval'),
        ('confirmed', 'Confirmed'),
        ('redo', 'Regenerate'),
        ('done', 'Done'),
        ('cancel', 'Cancelled')
    ], default='draft', tracking=True)

    purchase_line_ids = fields.One2many('purchase.requisition.line', 'requisition_id', string='Materials')
    quotation_line_ids = fields.One2many('quotation.requisition.line', 'requisition_id', string='Materials')
    budget_id = fields.Many2one('custom.budget', string='Generated Budget', readonly=True)
    po_count = fields.Integer(string='PO Count', compute='_compute_po_count')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('purchase.requisition.form') or 'New'
        return super().create(vals_list)

    @api.depends('purchase_order_ids')
    def _compute_po_count(self):
        for rec in self:
            rec.po_count = len(rec.purchase_order_ids)

    def action_approve(self):
        if not self.env.user.has_group('am_so_to_mrp.group_budget_approver'):
            raise UserError(_("You do not have the rights to approve this Requisition."))
        self.write({'state': 'confirmed'})
        if self.quotation_line_ids:
            self.quotation_line_ids.write({'approval_on': fields.Date.today()})
        return True

    def _has_active_po(self, product_id):
        """Check if there's an active (non-cancelled) PO line for this product in this PR."""
        return self.env['purchase.order.line'].search_count([
            ('order_id.pr_order_id', '=', self.id),
            ('product_id', '=', product_id),
            ('order_id.state', '!=', 'cancel')
        ])

    def action_custom_save(self):
        """
        Triggered by the custom Save button.
        Note: Odoo's UI automatically writes pending changes to the database
        before executing this method. We just return True to stay on the form.
        """
        return True


    def _validate_confirmed_quote(self, line):
        """Raise if the line has no confirmed quote."""
        if not (line.confirmed_quote_uom_price and line.confirmed_quote_vendor_id):
            raise UserError(_("No Quotation found for product %s") % line.product_id.display_name)

    # In PurchaseRequisition

    def action_confirm(self):
        errors = []
        for order in self:
            for line in order.quotation_line_ids:
                # Validate confirmed quote without raising
                if not (line.confirmed_quote_uom_price and line.confirmed_quote_vendor_id):
                    errors.append(_("No Quotation found for product %s") % line.product_id.display_name)
                    continue
                vendor = line.confirmed_quote_vendor_id
                price = line.confirmed_quote_uom_price
                if not vendor or not price:
                    continue
                if order._has_active_po(line.product_id.id):
                    continue
                # Catch the UserError from _check_price_increase instead of letting it propagate
                try:
                    line._check_price_increase(price, line.remarks)
                except UserError as e:
                    errors.append(str(e))

        if errors:
            # Return notification — transaction commits, data is preserved
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Cannot Confirm'),
                    'message': '\n'.join(errors),
                    'type': 'danger',
                    'sticky': True,
                }
            }

        # No errors — proceed normally
        for order in self:
            new_state = 'waiting' if any(
                line.checked_price_1 or line.checked_price_2 or line.checked_price_3
                for line in order.quotation_line_ids
            ) else 'confirmed'
            order.write({'state': new_state})

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_view_purchase_orders(self):
        self.ensure_one()
        action = {
            'name': _('Purchase Orders'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('pr_order_id', '=', self.id)],
            'context': {'default_pr_order_id': self.id},
        }
        if len(self.purchase_order_ids) == 1:
            action.update({'view_mode': 'form', 'res_id': self.purchase_order_ids.id})
        return action

    def action_open_budget(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Budget'),
            'res_model': 'custom.budget',
            'res_id': self.budget_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # --- UNIFIED QUOTATION CONFIRMATION ---
    def action_confirm_quotes(self):
        errors = []

        # --- VALIDATION PHASE (no UserError raised) ---
        for rec in self:
            if rec.state == 'redo':
                lines_to_process = rec.quotation_line_ids.filtered(lambda l: l.is_po_cancelled)
            else:
                lines_to_process = rec.quotation_line_ids

            for line in lines_to_process:
                if not (line.confirmed_quote_uom_price and line.confirmed_quote_vendor_id):
                    errors.append(_("No Quotation found for product %s") % line.product_id.display_name)
                    continue
                try:
                    line._check_price_increase(line.confirmed_quote_uom_price, line.remarks)
                except UserError as e:
                    errors.append(str(e))

        # If ANY errors — return notification, transaction still commits, data preserved
        if errors:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Cannot Confirm Quotes'),
                    'message': '\n'.join(errors),
                    'type': 'danger',
                    'sticky': True,
                }
            }

        # --- EXECUTION PHASE (only reached if no errors) ---
        for rec in self:
            if rec.state == 'redo':
                lines_to_process = rec.quotation_line_ids.filtered(lambda l: l.is_po_cancelled)
            else:
                lines_to_process = rec.quotation_line_ids

            vendor_lines = {}
            for line in lines_to_process:
                vendor = line.confirmed_quote_vendor_id
                price = line.confirmed_quote_uom_price
                if not vendor or not price:
                    continue

                vendor_lines.setdefault(vendor.id, []).append((0, 0, {
                    'product_id': line.product_id.id,
                    'product_qty': line.quantity_required,
                    'price_unit': price,
                    'remarks': line.remarks,
                }))
                line.is_po_cancelled = False

            if vendor_lines:
                rec._create_pos_from_selection(vendor_lines)

            rec.write({'state': 'done'})

        return self.action_view_purchase_orders()
    
    def _check_stock_status(self):
        """Checks if ALL active POs linked to this PR are fully received."""
        for line in self.quotation_line_ids:
            if line.is_po_cancelled:
                continue
        for pr in self:
            if not pr.order_number:
                continue
            if pr.order_number.stock_ready:
                continue  # Already flagged as stock up

            active_pos = pr.purchase_order_ids.filtered(lambda p: p.state != 'cancel')
            if not active_pos:
                continue  # No active POs to check

            all_done = True
            for po in active_pos:
                if po.state != 'purchase':
                    all_done = False
                    break
                for line in po.order_line:
                    # If any line hasn't been fully received into WH/Stock, it's not done
                    if line.qty_received < line.product_qty:
                        all_done = False
                        break
                if not all_done:
                    break

            if all_done:
                pr.order_number.stock_ready = True

    def _create_pos_from_selection(self, vendor_lines):
        for vendor_id, lines in vendor_lines.items():
            existing_po = self.env['purchase.order'].search([
                ('pr_order_id', '=', self.id), ('partner_id', '=', vendor_id), ('state', '=', 'draft')
            ], limit=1)
            if existing_po:
                existing_po.write({'order_line': lines})
            else:
                self.env['purchase.order'].create({
                    'partner_id': vendor_id,
                    'order_line': lines,
                    'pr_order_id': self.id,
                    'origin': self.name,
                })


class PurchaseRequisitionLine(models.Model):
    _name = 'purchase.requisition.line'
    _description = 'Purchase Requisition Lines'
    _order = 'sequence, id'

    requisition_id = fields.Many2one('purchase.requisition.form', string='Requisition', ondelete='cascade')
    sequence = fields.Integer(string='S.No', default=10)
    product_id = fields.Many2one('product.product', string='Material Name', required=True)
    quantity_required = fields.Float(string='Qty', default=1.0)
    uom_id = fields.Many2one('uom.uom', string='UOM', related='product_id.uom_id', store=True)

    third_last_price = fields.Char(string='3rd Last Purchase Price')
    third_last_vendor_id = fields.Many2one('res.partner', string='3rd Last Purchase Vendor')
    second_last_price = fields.Char(string='2nd Last Purchase Price')
    second_last_vendor_id = fields.Many2one('res.partner', string='2nd Last Purchase Vendor')
    first_last_price = fields.Char(string='1st Last Purchase Price')
    first_last_vendor_id = fields.Many2one('res.partner', string='1st Last Purchase Vendor')
    finished_good_id = fields.Many2many('product.product', string='Finished Good')


class QuotationRequisitionLine(models.Model):
    _name = 'quotation.requisition.line'
    _description = 'Quotation Requisition Lines'
    _order = 'sequence, id'

    # Fields copied from existing line when product is duplicated
    _CLONE_COPY_FIELDS = [
        'uom_id', 'first_quotation_price', 'first_quotation_vendor_id',
        'second_quotation_price', 'second_quotation_vendor_id',
        'third_quotation_price', 'third_quotation_vendor_id', 'remarks',
    ]
    
    finished_good_id = fields.Many2many('product.product', string='Finished Good')

    requisition_id = fields.Many2one('purchase.requisition.form', string='Requisition', ondelete='cascade')
    sequence = fields.Integer(string='S.No', default=10)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    quantity_required = fields.Float(string='Qty', default=1.0)
    uom_id = fields.Many2one('uom.uom', string='UOM', related='product_id.uom_id', store=True)

    checked_price_1 = fields.Boolean(string='1st Price Checked', compute='_compute_checked_prices', store=True)
    checked_price_2 = fields.Boolean(string='2nd Price Checked', compute='_compute_checked_prices', store=True)
    checked_price_3 = fields.Boolean(string='3rd Price Checked', compute='_compute_checked_prices', store=True)

    is_po_cancelled = fields.Boolean(string='PO Cancelled', default=False, readonly=True,
                                     help="Checked if the linked PO for this product was cancelled.")

    first_quotation_price = fields.Float(string='1st Quotation UOM Price')
    first_quotation_vendor_id = fields.Many2one('res.partner', string='1st Quotation Vendor')

    second_quotation_price = fields.Float(string='2nd Quotation UOM Price')
    second_quotation_vendor_id = fields.Many2one('res.partner', string='2nd Quotation Vendor')

    third_quotation_price = fields.Float(string='3rd Quotation UOM Price')
    third_quotation_vendor_id = fields.Many2one('res.partner', string='3rd Quotation Vendor')

    confirmed_quote_uom_price = fields.Float(string='Confirmed Quotation UOM Price')
    confirmed_quote_vendor_id = fields.Many2one('res.partner', string='Confirmed Quotation Vendor')

    remarks = fields.Char(string='Remarks')
    approval_on = fields.Date(string='Approval On')

    # Domain Restriction Fields
    restrict_products = fields.Boolean(string='Restrict Products', compute='_compute_allowed_products')
    allowed_product_ids = fields.Many2many(
        comodel_name='product.product',
        string='Allowed Products',
        compute='_compute_allowed_products'
    )

    # Split Tracking Fields (STORABLE)
    base_qty = fields.Float(string='Absolute Base Quantity', store=True)
    source_line_id = fields.Many2one('quotation.requisition.line', string='Source Line', store=True)
    clone_base_qty = fields.Float(string='Clone Base Quantity', store=True)

    # ======================================================================
    # Helpers
    # ======================================================================
    def _get_highest_recent_price(self):
        """Highest price_unit among the last 3 confirmed PO lines for this product."""
        prev_lines = self.env['purchase.order.line'].search([
            ('product_id', '=', self.product_id.id),
            ('order_id.state', '=', 'purchase')
        ], order='date_approve desc', limit=3)
        return max(prev_lines.mapped('price_unit')) if prev_lines else 0.0

    def _is_price_higher_than_recent(self, vendor, price):
        """Return True if the given price exceeds the highest recent PO price."""
        if not (self.product_id and vendor and price):
            return False
        highest_price = self._get_highest_recent_price()
        return bool(highest_price and price > highest_price)

    def unlink(self):
        """When a cloned line is deleted, return its quantity to the source line."""
        for line in self:
            source = line.source_line_id
            # Only add back if the source exists and isn't being deleted at the same time
            if source and source not in self:
                source.quantity_required += line.quantity_required
        return super().unlink()

    def _check_price_increase(self, price, remarks):
        """Raise if price exceeds highest recent PO price and no remarks are provided."""
        # Create a list of non-zero quotation prices
        valid_prices = [
            p for p in (self.first_quotation_price, self.second_quotation_price, self.third_quotation_price)
            if p > 0
        ]

        # Calculate min_price only from non-zero values if they exist
        min_price = min(valid_prices) if valid_prices else 0

        if min_price and self.confirmed_quote_uom_price != min_price and not self.remarks:
            raise UserError(
                _("Cheaper Vendor Options are available for product %s.\nKindly State Reason for confirming higher priced quotation in the remarks section.") % self.product_id.display_name)

        highest_price = self._get_highest_recent_price()
        if highest_price and price > highest_price and not remarks:
            raise UserError(_("Price increase for %s! Please add remarks.") % self.product_id.display_name)

    # ======================================================================
    # Compute Methods
    # ======================================================================
    @api.depends('requisition_id', 'requisition_id.quotation_line_ids.product_id')
    def _compute_allowed_products(self):
        for line in self:
            other_lines = line.requisition_id and line.requisition_id.quotation_line_ids.filtered(
                lambda l: l != line and l.product_id
            )
            if other_lines:
                line.restrict_products = True
                line.allowed_product_ids = other_lines.mapped('product_id')
            else:
                line.restrict_products = False
                line.allowed_product_ids = [(5, 0, 0)]

    @api.depends(
        'product_id', 'remarks',
        'first_quotation_vendor_id', 'first_quotation_price',
        'second_quotation_vendor_id', 'second_quotation_price',
        'third_quotation_vendor_id', 'third_quotation_price'
    )
    def _compute_checked_prices(self):
        for line in self:
            line.checked_price_1 = line._is_price_higher_than_recent(line.first_quotation_vendor_id, line.first_quotation_price)
            line.checked_price_2 = line._is_price_higher_than_recent(line.second_quotation_vendor_id, line.second_quotation_price)
            line.checked_price_3 = line._is_price_higher_than_recent(line.third_quotation_vendor_id, line.third_quotation_price)

    # ======================================================================
    # Onchange Handlers
    # ======================================================================
    @api.onchange('product_id')
    def _onchange_product_id_clone_from_existing(self):
        if not self.product_id or not self.requisition_id:
            self.source_line_id = False
            self.clone_base_qty = 0
            return

        existing_line = next(
            (c for c in self.requisition_id.quotation_line_ids
             if c.product_id == self.product_id and c != self),
            None
        )

        if not existing_line:
            self.source_line_id = False
            self.clone_base_qty = 0
            return

        # Copy everything EXCEPT quantity, confirmed_quote_uom_price, confirmed_quote_vendor_id
        for fname in self._CLONE_COPY_FIELDS:
            setattr(self, fname, getattr(existing_line, fname))

        # Setup source tracking
        self.source_line_id = existing_line

        # Get the absolute base quantity. If the source was already split, use its base_qty.
        base = existing_line.base_qty if existing_line.base_qty else existing_line.quantity_required
        existing_line.base_qty = base  # Lock the base qty on the source!

        self.clone_base_qty = base
        self.base_qty = 0.0
        self.quantity_required = 0.0

    @api.onchange('quantity_required')
    def _onchange_quantity_required_split(self):
        # Check if this line is a source for any other line
        is_source = bool(self.requisition_id and self.requisition_id.quotation_line_ids.filtered(
            lambda l: l.source_line_id == self and l != self
        ))

        if self.source_line_id and self.clone_base_qty:
            # This line IS a clone
            if self.quantity_required < 0:
                self.quantity_required = 0.0

            # Calculate total clone qty for this source (including self)
            total_clone_qty = self.quantity_required + sum(
                l.quantity_required for l in self.requisition_id.quotation_line_ids
                if l.source_line_id == self.source_line_id and l != self
            )

            # Cap the quantity if total exceeds base
            if total_clone_qty > self.clone_base_qty:
                self.quantity_required = self.clone_base_qty - (total_clone_qty - self.quantity_required)
                total_clone_qty = self.clone_base_qty

            # Simultaneously update the source line's UI
            self.source_line_id.quantity_required = self.clone_base_qty - total_clone_qty

        elif not is_source:
            # This line is standalone. Sync its base_qty.
            self.base_qty = self.quantity_required

    # ======================================================================
    # Quote Setters
    # ======================================================================
    # In QuotationRequisitionLine

    def _set_confirmed_quote(self, price, vendor):
        if not price or not vendor:
            # Return a notification instead of raising UserError
            # This allows the transaction to commit, preserving typed data
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Missing Data'),
                    'message': _('Price or vendor not defined for product %s') % self.product_id.display_name,
                    'type': 'warning',
                    'sticky': False,
                }
            }
        self.confirmed_quote_uom_price = price
        self.confirmed_quote_vendor_id = vendor.id

    def first_quote(self):
        return self._set_confirmed_quote(self.first_quotation_price, self.first_quotation_vendor_id)

    def second_quote(self):
        return self._set_confirmed_quote(self.second_quotation_price, self.second_quotation_vendor_id)

    def third_quote(self):
        return self._set_confirmed_quote(self.third_quotation_price, self.third_quotation_vendor_id)

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    qc_failed = fields.Boolean(string='QC Failed', default=False)
    remarks = fields.Text(string='Reason for Price Increase')


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    pr_order_id = fields.Many2one('purchase.requisition.form', string='Purchase Requisition')
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms')

    state = fields.Selection(selection_add=[
        ('locked', 'Locked')
    ], ondelete={'locked': 'set default'})

    def action_custom_lock(self):
        """Transition the record state to 'locked' to trigger global read-only rules."""
        for order in self:
            if order.state == 'draft':
                order.write({'state': 'locked'})
                order.message_post(body="Purchase Order status manually changed to Locked.")

    def action_custom_unlock(self):
        """Revert the lock state back into a regular active Purchase Order."""
        for order in self:
            if order.state == 'locked':
                order.write({'state': 'draft'})
                order.message_post(body="Purchase Order unlocked for modifications.")

    def _button_redo(self):
        for order in self:
            if not order.pr_order_id:
                continue

            # Count total lines in the PO
            total_lines_count = len(order.order_line)

            # Filter the lines where qc_failed is True
            failed_lines = order.order_line.filtered('qc_failed')
            failed_lines_count = len(failed_lines)

            # 1. Extract unique products from failed lines
            cancelled_products = failed_lines.mapped('product_id')

            # 2. Find matching quote lines and clear the confirmed price and vendor
            order.pr_order_id.quotation_line_ids.filtered(
                lambda l: l.product_id in cancelled_products
            ).write({
                'is_po_cancelled': True,
                'confirmed_quote_uom_price': 0,
                'confirmed_quote_vendor_id': False
            })

            if order.pr_order_id.state == 'done':
                order.pr_order_id.write({'state': 'redo'})

            # 3. If ALL lines failed QC, cancel the Purchase Order entirely
            if total_lines_count > 0 and total_lines_count == failed_lines_count:
                if hasattr(order, 'button_cancel'):
                    order.button_cancel()
                elif hasattr(order, 'action_cancel'):
                    order.action_cancel()

    def button_confirm(self):
        if not self.payment_term_id:
            raise UserError(_('Payment Terms not defined. Notify the Finance Team to keep the flow running.'))

        # 1. Record existing pickings before the confirmation process
        existing_pickings = self.picking_ids

        # 2. Call the standard Odoo confirm process (creates new pickings)
        res = super().button_confirm()

        # 3. Filter for newly created incoming pickings only and update their state
        incoming_new_pickings = (self.picking_ids - existing_pickings).filtered(
            lambda p: p.picking_type_id.code == 'incoming'
        )
        if incoming_new_pickings:
            incoming_new_pickings.write({'state': 'vehicle_inspection'})

        return res

    def action_view_purchase_requisition(self):
        self.ensure_one()
        if not self.pr_order_id:
            return True
        return {
            'name': _('Purchase Requisition'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.requisition.form',
            'view_mode': 'form',
            'res_id': self.pr_order_id.id,
            'target': 'current',
        }


class ResPartner(models.Model):
    _inherit = 'res.partner'

    purchase_line_ids = fields.One2many('purchase.order', 'partner_id', string='Purchase Orders')