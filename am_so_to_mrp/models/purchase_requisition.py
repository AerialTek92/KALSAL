from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup
from lxml import etree  # add to imports

# Skipped by the blanket-readonly pass; the duplicated field nodes in the
# XML already carry their own group/state modifiers.
LOCKED_VIEW_EXCEPTIONS = {'payment_term_id', 'currency_id', 'state'}


def get_view(self, view_id=None, view_type='form', **options):
    """While locked, grey out every field on the PO form except
    Payment Terms and Currency (Finance's two writable fields)."""
    res = super().get_view(view_id=view_id, view_type=view_type, **options)
    if view_type != 'form':
        return res

    doc = etree.XML(res)

    # Don't touch cells inside the embedded order_line sub-view; the
    # readonly modifier on the order_line field itself freezes the whole widget.
    subview_fields = set()
    for node in doc.xpath("//field[@name='order_line']"):
        subview_fields.update(node.xpath('.//field'))

    for field in doc.iter('field'):
        if field in subview_fields:
            continue
        if field.get('name') in self.LOCKED_VIEW_EXCEPTIONS:
            continue
        current = field.get('readonly')
        if current is None or current.strip().lower() in ('', '0', 'false'):
            field.set('readonly', "state == 'locked'")
        elif current.strip().lower() in ('1', 'true'):
            continue  # already always-readonly, leave it
        else:
            # preserve whatever condition was already there
            field.set('readonly', f"({current}) or state == 'locked'")

    return etree.tostring(doc, encoding='unicode')


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

    def _notify_approvers_pr_waiting(self):
        target_group = self.env.ref('am_so_to_mrp.group_budget_approver', raise_if_not_found=False)
        if not target_group:
            return
        target_partners = self.env['res.users'].search(
            [('all_group_ids', 'in', [target_group.id])]
        ).mapped('partner_id')
        if not target_partners:
            return

        base_url = self._get_current_base_url()
        notify_ctx = dict(self.env.context, mail_notify_force_send=False)

        for pr in self:
            url = f"{base_url}/web#id={pr.id}&model=purchase.requisition.form&view_type=form"
            title = _("Purchase Requisition Needs Approval")
            plain_msg = _(
                "Purchase Requisition %s has prices requiring your approval."
            ) % pr.name
            html_msg = Markup(_(
                "Purchase Requisition <b>%s</b> has prices requiring your approval.<br/><br/>"
                "<a href='%s' target='_blank'><b>Click here to review and approve</b></a>."
            )) % (pr.name, url)

            if hasattr(pr, 'message_notify'):
                pr.with_context(notify_ctx).message_notify(
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            else:
                self.env['mail.thread'].with_context(notify_ctx).message_notify(
                    model='purchase.requisition.form', res_id=pr.id,
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            for partner in target_partners:
                self.env['bus.bus']._sendone(
                    partner, 'simple_notification',
                    {'type': 'warning', 'title': title, 'message': plain_msg, 'sticky': True}
                )

    def _notify_procurement_users_pr_approved(self):
        target_partners = self._get_group_partners('fk_kalsal_security.group_executor_procurement')
        if not target_partners:
            return

        base_url = self._get_current_base_url()
        notify_ctx = dict(self.env.context, mail_notify_force_send=False)

        for pr in self:
            url = f"{base_url}/web#id={pr.id}&model=purchase.requisition.form&view_type=form"
            title = _("Purchase Requisition Approved")
            plain_msg = _(
                "Purchase Requisition %s has been approved. Kindly confirm the quotes to generate Purchase Orders."
            ) % pr.name
            html_msg = Markup(_(
                "Purchase Requisition <b>%s</b> has been approved.<br/><br/>"
                "Kindly confirm the quotes to generate the Purchase Orders. "
                "<a href='%s' target='_blank'><b>Click here to open the Requisition</b></a>."
            )) % (pr.name, url)

            if hasattr(pr, 'message_notify'):
                pr.with_context(notify_ctx).message_notify(
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            else:
                self.env['mail.thread'].with_context(notify_ctx).message_notify(
                    model='purchase.requisition.form', res_id=pr.id,
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            for partner in target_partners:
                self.env['bus.bus']._sendone(
                    partner, 'simple_notification',
                    {'type': 'success', 'title': title, 'message': plain_msg, 'sticky': True}
                )

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
        self._notify_procurement_users_pr_approved()
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
        # No errors — proceed normally
        for order in self:
            new_state = 'waiting' if any(
                line.checked_price_1 or line.checked_price_2 or line.checked_price_3
                for line in order.quotation_line_ids
            ) else 'confirmed'
            order.write({'state': new_state})

            if new_state == 'waiting':
                order._notify_approvers_pr_waiting()
            else:
                order._notify_procurement_users_pr_approved()

            for line in order.quotation_line_ids:
                if not (line.confirmed_quote_uom_price and line.confirmed_quote_vendor_id):
                    errors.append(
                        _("No Quotation found for product: %s") % line.product_id.display_name
                    )
                    continue
                vendor = line.confirmed_quote_vendor_id
                price = line.confirmed_quote_uom_price
                if not vendor or not price:
                    continue
                if order._has_active_po(line.product_id.id):
                    continue
                try:
                    line._check_price_increase(price, line.remarks)
                except UserError as e:
                    errors.append(str(e))

        if errors:
            # Send each error as a SEPARATE notification using _sendone in a loop
            for error in errors:
                self.env['bus.bus']._sendone(
                    self.env.user.partner_id,
                    'simple_notification',
                    {
                        'title': _('Cannot Confirm'),
                        'message': error,
                        'type': 'danger',
                        'sticky': True,
                    }
                )
            return False  # Stay on current record

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

        # --- VALIDATION PHASE ---
        for rec in self:
            if rec.state == 'redo':
                lines_to_process = rec.quotation_line_ids.filtered(lambda l: l.is_po_cancelled)
            else:
                lines_to_process = rec.quotation_line_ids

            for line in lines_to_process:
                if not (line.confirmed_quote_uom_price and line.confirmed_quote_vendor_id):
                    errors.append(
                        _("No Quotation found for product: %s") % line.product_id.display_name
                    )
                    continue
                try:
                    line._check_price_increase(line.confirmed_quote_uom_price, line.remarks)
                except UserError as e:
                    errors.append(str(e))

        if errors:
            # Send each error as a SEPARATE notification using _sendone in a loop
            for error in errors:
                self.env['bus.bus']._sendone(
                    self.env.user.partner_id,
                    'simple_notification',
                    {
                        'title': _('Cannot Confirm Quotes'),
                        'message': error,
                        'type': 'danger',
                        'sticky': True,
                    }
                )
            return False  # Stay on current record

        # --- EXECUTION PHASE ---
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

    def _get_current_base_url(self):
        """URL of the server actually handling this request, falling back to
        detecting the machine's live network IP when there's no HTTP request
        context (e.g. a cron job)."""
        try:
            from odoo.http import request
            if request:
                return request.httprequest.host_url.rstrip('/')
        except RuntimeError:
            pass

        import socket
        ip = '127.0.0.1'
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError:
            pass

        port = self.env['ir.config_parameter'].sudo().get_param('http_port') or '8069'
        return f'http://{ip}:{port}'

    def _get_group_partners(self, group_xmlid):
        """Resolve a security group to its member partners, excluding Factory
        Admin/CEO and Operations users — they inherit Production access as
        part of their broader role, but shouldn't be pulled into
        department-level operational notifications."""
        target_group = self.env.ref(group_xmlid, raise_if_not_found=False)
        if not target_group:
            return self.env['res.partner']

        excluded_groups = self.env['res.groups']
        for xmlid in ('fk_kalsal_security.group_factory_admin', 'fk_kalsal_security.group_operations'):
            grp = self.env.ref(xmlid, raise_if_not_found=False)
            if grp:
                excluded_groups |= grp

        users = self.env['res.users'].search([('all_group_ids', 'in', [target_group.id])])
        if excluded_groups:
            users = users.filtered(lambda u: not (u.all_group_ids & excluded_groups))
        return users.mapped('partner_id')

    def _notify_production_users_stock_ready(self, sale_order):
        target_partners = self._get_group_partners('fk_kalsal_security.group_executor_production')
        if not target_partners:
            return

        base_url = self._get_current_base_url()

        action = self.env['ir.actions.act_window'].sudo().create({
            'name': _('New Material Requisition Slip - %s') % sale_order.name,
            'res_model': 'material.requisition.slip',
            'view_mode': 'form',
            'target': 'current',
            'context': "{'default_sale_order_id': %d}" % sale_order.id,
        })
        mrs_new_url = f"{base_url}/web#action={action.id}"

        title = _("Stock Ready - MRS Required")
        plain_msg = _(
            "Stock is ready for Sale Order %s. Kindly create the Material Requisition Slip."
        ) % sale_order.name
        html_msg = Markup(_(
            "Stock is ready for Sale Order <b>%s</b>.<br/><br/>"
            "Kindly create the Material Requisition Slip for it. "
            "<a href='%s' target='_blank'><b>Click here to create a new Material Requisition Slip</b></a>."
        )) % (sale_order.name, mrs_new_url)

        notify_ctx = dict(self.env.context, mail_notify_force_send=False)
        if hasattr(sale_order, 'message_notify'):
            sale_order.with_context(notify_ctx).message_notify(
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        else:
            self.env['mail.thread'].with_context(notify_ctx).message_notify(
                model='sale.order', res_id=sale_order.id,
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        for partner in target_partners:
            self.env['bus.bus']._sendone(
                partner, 'simple_notification',
                {'type': 'warning', 'title': title, 'message': plain_msg, 'sticky': True}
            )

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
                pr._notify_production_users_stock_ready(pr.order_number)

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
                    'sale_order_id': self.order_number.id,
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

    @api.depends(
        'product_id',
        'first_quotation_vendor_id', 'first_quotation_price',
        'second_quotation_vendor_id', 'second_quotation_price',
        'third_quotation_vendor_id', 'third_quotation_price'
    )
    def _compute_checked_prices(self):
        for line in self:
            line.checked_price_1 = line._is_price_higher_than_recent(line.first_quotation_vendor_id,
                                                                     line.first_quotation_price)
            line.checked_price_2 = line._is_price_higher_than_recent(line.second_quotation_vendor_id,
                                                                     line.second_quotation_price)
            line.checked_price_3 = line._is_price_higher_than_recent(line.third_quotation_vendor_id,
                                                                     line.third_quotation_price)

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

    def _assert_unlocked(self):
        if any(line.order_id.state == 'locked' for line in self) \
                and not self.env.context.get('bypass_lock_check'):
            raise UserError(_(
                "This Purchase Order is locked pending Finance's confirmation of "
                "Payment Terms. Lines cannot be added, modified or removed until "
                "Finance releases it."
            ))

    def write(self, vals):
        self._assert_unlocked()
        return super().write(vals)

    def unlink(self):
        self._assert_unlocked()
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._assert_unlocked()
        return lines


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    pr_order_id = fields.Many2one('purchase.requisition.form', string='Purchase Requisition')
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms')
    sale_order_id = fields.Many2one('sale.order', string='Sale Order')

    # FIX: the old selection_add key was 'finance' while the code below wrote
    # 'locked' - that value did not exist in the selection, so calling
    # action_custom_lock() would raise a ValueError. Key now matches usage.
    state = fields.Selection(
        selection_add=[('locked', 'Locked - Pending Finance')],
        ondelete={'locked': 'set default'}
    )

    # The only fields Finance may touch while the order is locked.
    FINANCE_EDITABLE_FIELDS = {'payment_term_id', 'currency_id'}

    def _get_current_base_url(self):
        """URL of the server actually handling this request, falling back to
        detecting the machine's live network IP when there's no HTTP request
        context (e.g. a cron job)."""
        try:
            from odoo.http import request
            if request:
                return request.httprequest.host_url.rstrip('/')
        except RuntimeError:
            pass

        import socket
        ip = '127.0.0.1'
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError:
            pass

        port = self.env['ir.config_parameter'].sudo().get_param('http_port') or '8069'
        return f'http://{ip}:{port}'

    def _notify_group(self, group_xmlid, title, plain_msg, html_msg):
        target_group = self.env.ref(group_xmlid, raise_if_not_found=False)
        if not target_group:
            return

        excluded_groups = self.env['res.groups']
        for excl_xmlid in ('fk_kalsal_security.group_factory_admin', 'fk_kalsal_security.group_operations'):
            grp = self.env.ref(excl_xmlid, raise_if_not_found=False)
            if grp:
                excluded_groups |= grp

        users = self.env['res.users'].search([('all_group_ids', 'in', [target_group.id])])
        if excluded_groups:
            users = users.filtered(lambda u: not (u.all_group_ids & excluded_groups))
        target_partners = users.mapped('partner_id')
        if not target_partners:
            return

        notify_ctx = dict(self.env.context, mail_notify_force_send=False)
        for order in self:
            if hasattr(order, 'message_notify'):
                order.with_context(notify_ctx).message_notify(
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            else:
                self.env['mail.thread'].with_context(notify_ctx).message_notify(
                    model='purchase.order', res_id=order.id,
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            for partner in target_partners:
                self.env['bus.bus']._sendone(
                    partner, 'simple_notification',
                    {'type': 'warning', 'title': title, 'message': plain_msg, 'sticky': True}
                )

    def _notify_finance_users_po_submitted(self):
        base_url = self._get_current_base_url()
        for order in self:
            url = f"{base_url}/web#id={order.id}&model=purchase.order&view_type=form"
            title = _("Payment Terms Required")
            plain_msg = _(
                "Purchase Order %s has been submitted for Payment Terms confirmation."
            ) % order.name
            html_msg = Markup(_(
                "Purchase Order <b>%s</b> has been submitted for Payment Terms confirmation.<br/><br/>"
                "Kindly add the Payment Terms and release it back to Procurement. "
                "<a href='%s' target='_blank'><b>Click here to open the Purchase Order</b></a>."
            )) % (order.name, url)
            order._notify_group('fk_kalsal_security.group_executor_finance', title, plain_msg, html_msg)

    def _notify_procurement_users_po_released(self):
        base_url = self._get_current_base_url()
        for order in self:
            url = f"{base_url}/web#id={order.id}&model=purchase.order&view_type=form"
            title = _("Purchase Order Ready to Confirm")
            plain_msg = _(
                "Payment Terms have been confirmed for Purchase Order %s. Kindly confirm the order."
            ) % order.name
            html_msg = Markup(_(
                "Payment Terms have been confirmed for Purchase Order <b>%s</b>.<br/><br/>"
                "Kindly confirm the order. "
                "<a href='%s' target='_blank'><b>Click here to open the Purchase Order</b></a>."
            )) % (order.name, url)
            order._notify_group('fk_kalsal_security.group_executor_procurement', title, plain_msg, html_msg)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def action_custom_lock(self):
        """Procurement: submit the finished PO to Finance for Payment Terms review."""
        for order in self:
            if order.state != 'draft':
                raise UserError(_("Only Purchase Orders in Draft can be submitted to Finance."))
            order.with_context(bypass_lock_check=True).write({'state': 'locked'})
            order.message_post(body=_(
                "Purchase Order submitted to Finance for Payment Terms confirmation. "
                "It is now locked for editing."
            ))
            order._notify_finance_users_po_submitted()

    def action_finance_release(self):
        """Finance: confirm Payment Terms/Currency and hand the PO back to Procurement."""
        for order in self:
            if order.state != 'locked':
                raise UserError(_("This Purchase Order is not pending Finance review."))
            if not self.env.user.has_group('account.group_account_user'):
                raise UserError(_("Only Finance users can release this Purchase Order."))
            if not order.payment_term_id:
                raise UserError(_("Please set Payment Terms before releasing this order to Procurement."))
            order.with_context(bypass_lock_check=True).write({'state': 'draft'})
            order.message_post(body=_(
                "Payment Terms confirmed by Finance (%s). Purchase Order released back "
                "to Procurement to be confirmed."
            ) % (order.payment_term_id.name,))
            order._notify_procurement_users_po_released()

    def action_custom_unlock(self):
        """Manager-only escape hatch: force a locked PO back to Draft WITHOUT the
        Payment Terms check that action_finance_release enforces. Restrict this
        button in the view to a manager group."""
        for order in self:
            if order.state == 'locked':
                order.with_context(bypass_lock_check=True).write({'state': 'draft'})
                order.message_post(body=_("Purchase Order force-unlocked (Payment Terms not verified)."))

    # ------------------------------------------------------------------
    # Server-side enforcement - this is what actually protects the data,
    # independent of which view/screen/automation is doing the writing.
    # ------------------------------------------------------------------
    def write(self, vals):
        if self.env.context.get('bypass_lock_check'):
            return super().write(vals)

        touched_fields = set(vals.keys()) - {'state', 'message_follower_ids', 'activity_ids','access_token'}
        if touched_fields:
            is_finance = self.env.user.has_group('account.group_account_user')
            for order in self:
                if order.state != 'locked':
                    continue
                if not is_finance:
                    raise UserError(_(
                        "This Purchase Order is locked pending Finance's confirmation of "
                        "Payment Terms. No changes are allowed until Finance releases it."
                    ))
                extra_fields = touched_fields - self.FINANCE_EDITABLE_FIELDS
                if extra_fields:
                    raise UserError(_(
                        "While this Purchase Order is locked, Finance can only update "
                        "Payment Terms and Currency."
                    ))
        return super().write(vals)

    def button_cancel(self):
        res = super().button_cancel()
        self._button_redo()
        return res

    def _button_redo(self):
        for order in self:
            if not order.pr_order_id:
                continue

            # Check if there are any active GRNs (transfers) associated with the PO
            # We filter out 'cancel' states to ensure we are only looking for active/done receipts
            active_grn = order.picking_ids.filtered(lambda p: p.state != 'cancel')

            total_lines_count = len(order.order_line)

            if not active_grn:
                # If no active GRN exists, treat all lines as failed lines
                failed_lines = order.order_line
            else:
                # Default flow: only count lines that actually failed the QC check
                failed_lines = order.order_line.filtered('qc_failed')

            failed_lines_count = len(failed_lines)
            cancelled_products = failed_lines.mapped('product_id')

            order.pr_order_id.quotation_line_ids.filtered(
                lambda l: l.product_id in cancelled_products
            ).write({
                'is_po_cancelled': True,
                'confirmed_quote_uom_price': 0,
                'confirmed_quote_vendor_id': False
            })

            if total_lines_count > 0 and total_lines_count == failed_lines_count and active_grn:
                if hasattr(order, 'button_cancel'):
                    order.button_cancel()
                    order.pr_order_id.write({'state': 'redo'})

                elif hasattr(order, 'action_cancel'):
                    order.action_cancel()
                    order.pr_order_id.write({'state': 'redo'})


    def button_confirm(self):
        if not self.payment_term_id:
            raise UserError(_('Payment Terms not defined. Notify the Finance Team to keep the flow running.'))

        existing_pickings = self.picking_ids
        res = super().button_confirm()

        incoming_new_pickings = (self.picking_ids - existing_pickings).filtered(
            lambda p: p.picking_type_id.code == 'incoming'
        )

        if incoming_new_pickings:
            incoming_new_pickings.write({'state': 'vehicle_inspection'})
            incoming_new_pickings._notify_store_users_grn_generated()

        return res

    def unlink(self):
        if any(order.state == 'locked' for order in self):
            raise UserError(_(
                "A Purchase Order pending Finance review cannot be deleted."
            ))
        return super().unlink()

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
