from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup


class FgReporting(models.Model):
    _name = 'fg.reporting'
    _description = 'Finished Goods Reporting'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # ==========================================
    # HEADER
    # ==========================================
    name = fields.Char(
        string='Report No', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True)

    date = fields.Date(
        string='Issue Date', default=fields.Date.context_today,
        readonly=True, tracking=True)

    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', required=True, tracking=True,
        domain="[('id', 'in', allowed_sale_order_ids)]",
        help="Only Sale Orders with at least one product that passed Semi-Finished QC are selectable.")

    allowed_sale_order_ids = fields.Many2many(
        'sale.order', string='Allowed Sale Orders',
        compute='_compute_allowed_sale_order_ids')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
    ], string='Status', default='draft', tracking=True)

    picking_id = fields.Many2one(
        'stock.picking', string='Internal Transfer',
        readonly=True, copy=False,
        help="Production → Store transfer generated on confirmation.")

    line_ids = fields.One2many('fg.reporting.line', 'reporting_id', string='Finished Goods Lines')

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
        Admin/CEO (group_factory_admin covers both, since CEO implies it) and
        Operations users."""
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

    def _notify_quality_users_transfer_validated(self):
        self.ensure_one()
        target_partners = self._get_group_partners('fk_kalsal_security.group_management_quality')
        if not target_partners:
            return

        base_url = self._get_current_base_url()
        batch_names = ', '.join(self.line_ids.mapped('lot_id.name')) or _('N/A')

        action_vals = {
            'name': _('New FG Quality Check - %s') % self.name,
            'res_model': 'finished.qc',
            'view_mode': 'form',
            'target': 'current',
        }
        if self.sale_order_id:
            action_vals['context'] = "{'default_sale_order_id': %d}" % self.sale_order_id.id
        action = self.env['ir.actions.act_window'].sudo().create(action_vals)
        finished_qc_url = f"{base_url}/web#action={action.id}"

        title = _("Final QC Required")
        plain_msg = _(
            "Internal transfer for FG Report %s has been validated. Kindly complete the Final QC for batch %s."
        ) % (self.name, batch_names)
        html_msg = Markup(_(
            "The internal transfer for FG Report <b>%s</b> (Sale Order <b>%s</b>, batch <b>%s</b>) has been validated.<br/><br/>"
            "Kindly complete the Final QC for this batch. "
            "<a href='%s' target='_blank'><b>Click here to create a new FG Quality Check</b></a>."
        )) % (self.name, self.sale_order_id.name, batch_names, finished_qc_url)

        notify_ctx = dict(self.env.context, mail_notify_force_send=False)
        if hasattr(self, 'message_notify'):
            self.with_context(notify_ctx).message_notify(
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        else:
            self.env['mail.thread'].with_context(notify_ctx).message_notify(
                model='fg.reporting', res_id=self.id,
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        for partner in target_partners:
            self.env['bus.bus']._sendone(
                partner, 'simple_notification',
                {'type': 'success', 'title': title, 'message': plain_msg, 'sticky': True}
            )

    def _notify_store_users_fg_reporting_confirmed(self):
        self.ensure_one()
        if not self.picking_id:
            return

        target_partners = self._get_group_partners('fk_kalsal_security.group_executor_stores')
        if not target_partners:
            return

        base_url = self._get_current_base_url()
        url = f"{base_url}/web#id={self.picking_id.id}&model=stock.picking&view_type=form"

        title = _("FG Reporting Confirmed - Transfer Ready")
        plain_msg = _(
            "FG Report %s has been confirmed. Kindly validate the linked internal transfer to receive the batch."
        ) % self.name
        html_msg = Markup(_(
            "FG Report <b>%s</b> has been confirmed.<br/><br/>"
            "Kindly validate the linked internal transfer to mark receiving for the batch. "
            "<a href='%s' target='_blank'><b>Click here to open the Internal Transfer</b></a>."
        )) % (self.name, url)

        notify_ctx = dict(self.env.context, mail_notify_force_send=False)
        if hasattr(self, 'message_notify'):
            self.with_context(notify_ctx).message_notify(
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        else:
            self.env['mail.thread'].with_context(notify_ctx).message_notify(
                model='fg.reporting', res_id=self.id,
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        for partner in target_partners:
            self.env['bus.bus']._sendone(
                partner, 'simple_notification',
                {'type': 'warning', 'title': title, 'message': plain_msg, 'sticky': True}
            )

    # ==========================================
    # GATING: SOs with remaining passed SFG QC quantity
    # ==========================================
    def _compute_allowed_sale_order_ids(self):
        """Allow SOs that have at least one product whose passed SFG QC quantity
        exceeds the quantity already confirmed in FG Reports."""
        # 1. Aggregate passed SFG QC quantities per (SO, Product)
        passed_qcs = self.env['semi.finished.qc'].search([('state', '=', 'passed')])
        sfg_passed_map = {}
        for qc in passed_qcs:
            if qc.sale_order_id and qc.product_id:
                key = (qc.sale_order_id.id, qc.product_id.id)
                qty = qc.qc_passed_qty or qc.qty_to_qc
                sfg_passed_map[key] = sfg_passed_map.get(key, 0.0) + qty

        # 2. Aggregate confirmed FG Reporting quantities per (SO, Product)
        confirmed_reports = self.env['fg.reporting'].search([('state', '=', 'confirmed')])
        fg_reported_map = {}
        for rpt in confirmed_reports:
            if rpt.sale_order_id:
                for line in rpt.line_ids:
                    key = (rpt.sale_order_id.id, line.product_id.id)
                    # Use boxes_produced as it is the true base UoM quantity
                    qty = line.boxes_produced or (line.cartons_produced * (line.product_id.pcs_per_carton or 1.0))
                    fg_reported_map[key] = fg_reported_map.get(key, 0.0) + qty

        # 3. Determine which SOs still have unreported passed quantities
        allowed_sos = self.env['sale.order']
        so_ids_to_check = set(k[0] for k in sfg_passed_map.keys())
        for so in self.env['sale.order'].browse(list(so_ids_to_check)):
            for so_line in so.order_line:
                key = (so.id, so_line.product_id.id)
                passed = sfg_passed_map.get(key, 0.0)
                reported = fg_reported_map.get(key, 0.0)

                # If passed SFG quantity is strictly greater than what's already in confirmed FG reports
                if passed > reported:
                    allowed_sos |= so
                    break  # SO is allowed, no need to check other lines for this SO

        for rec in self:
            rec.allowed_sale_order_ids = allowed_sos

    # ==========================================
    # ONCHANGE: auto-build lines for products with remaining quantity
    # ==========================================
    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        """One line per SO product whose mixed MRS batches still have
        unreported quantity. 'To be produced' targets are fetched from the
        MRS batch quantities (a 'done' MRS = mixed batch), capped by the
        passed Semi-Finished QC quantity and the SO line quantity."""
        self.line_ids = [(5, 0, 0)]
        if not self.sale_order_id:
            return

        # 1. MRS batch quantities (mixed batches) per product
        done_mrs = self.env['material.requisition.slip'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('state', '=', 'done'),
        ])
        mrs_qty_map = {}
        for mrs in done_mrs:
            if mrs.recipe_product_id:
                mrs_qty_map[mrs.recipe_product_id.id] = \
                    mrs_qty_map.get(mrs.recipe_product_id.id, 0.0) + mrs.qty_producing

        # 2. Passed SFG QC qty per product (safety cap: never report more than QC released)
        passed_qcs = self.env['semi.finished.qc'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('state', '=', 'passed'),
        ])
        sfg_passed_map = {}
        for qc in passed_qcs:
            qty = qc.qc_passed_qty or qc.qty_to_qc
            sfg_passed_map[qc.product_id.id] = sfg_passed_map.get(qc.product_id.id, 0.0) + qty

        # 3. Already confirmed FG Reported qty per product
        confirmed_reports = self.env['fg.reporting'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('state', '=', 'confirmed'),
        ])
        fg_reported_map = {}
        for rpt in confirmed_reports:
            for line in rpt.line_ids:
                qty = line.boxes_produced or (line.cartons_produced * (line.product_id.pcs_per_carton or 1.0))
                fg_reported_map[line.product_id.id] = fg_reported_map.get(line.product_id.id, 0.0) + qty

        # 4. Build lines: remaining = min(MRS mixed, SFG passed, SO qty) - already reported
        lines = []
        sno = 1
        for so_line in self.sale_order_id.order_line:
            product = so_line.product_id
            mixed = mrs_qty_map.get(product.id, 0.0)
            passed = sfg_passed_map.get(product.id, 0.0)
            reported = fg_reported_map.get(product.id, 0.0)

            remaining_boxes = min(mixed, passed, so_line.product_uom_qty) - reported
            if remaining_boxes <= 0.0:
                continue

            ppc = product.pcs_per_carton or 1.0
            lines.append((0, 0, {
                'sno': sno,
                'product_id': product.id,
                'lot_id': self._fetch_lot_for_product(product).id or False,
                'boxes_to_be_produced': remaining_boxes,
                'cartons_to_be_produced': remaining_boxes / ppc,
            }))
            sno += 1

        if lines:
            self.line_ids = lines
        else:
            return {'warning': {
                'title': _('No Eligible Products'),
                'message': _(
                    'Sale Order %s has no remaining mixed quantity to report '
                    '(all done MRS batches have already been confirmed in FG Reports).'
                ) % self.sale_order_id.name,
            }}

    def _fetch_lot_for_product(self, product):
        """Fetch lot from MO first, fallback to newest stock.lot."""
        self.ensure_one()
        lot = self.env['stock.lot']
        if self.sale_order_id:
            so_name = self.sale_order_id.name
            mo = self.env['mrp.production'].search([
                ('origin', 'like', f'{so_name}%'),
                ('product_id', '=', product.id)
            ], limit=1, order='id desc')
            if mo and mo.lot_producing_ids:
                lot = mo.lot_producing_ids[0]
        if not lot:
            lot = self.env['stock.lot'].search([
                ('product_id', '=', product.id),
                ('company_id', '=', self.env.company.id)
            ], order='id desc', limit=1)
        return lot

    def action_confirm(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("No finished goods lines found. Please select a Sale Order first."))

            no_reason = rec.line_ids.filtered(
                lambda l: (l.cartons_produced != l.cartons_to_be_produced
                           or l.boxes_produced != l.boxes_to_be_produced)
                          and not l.reason_short_excess)
            if no_reason:
                raise UserError(rec._build_variance_error(no_reason))

            rec.write({'state': 'confirmed'})
            rec.message_post(body=_("<b>Finished Goods Reporting Confirmed.</b>"))
            rec._create_internal_transfer()
            rec._notify_store_users_fg_reporting_confirmed()

    def _build_variance_error(self, lines):
        """Builds a detailed validation error for Cartons/Boxes variance."""
        cartons_only = lines.filtered(
            lambda l: l.cartons_produced != l.cartons_to_be_produced and l.boxes_produced == l.boxes_to_be_produced)
        boxes_only = lines.filtered(
            lambda l: l.boxes_produced != l.boxes_to_be_produced and l.cartons_produced == l.cartons_to_be_produced)
        both = lines.filtered(
            lambda l: l.cartons_produced != l.cartons_to_be_produced and l.boxes_produced != l.boxes_to_be_produced)

        parts = [_("Reporting Validation Error:")]

        if cartons_only:
            parts.append(_("\n\n❌ Cartons variance:\n• %s") % '\n• '.join([
                "%s (To be: %s | Produced: %s)" % (l.product_id.name, l.cartons_to_be_produced, l.cartons_produced) for
                l in cartons_only]))
        if boxes_only:
            parts.append(_("\n\n❌ Boxes variance:\n• %s") % '\n• '.join([
                "%s (To be: %s | Produced: %s)" % (l.product_id.name, l.boxes_to_be_produced, l.boxes_produced) for l in
                boxes_only]))
        if both:
            parts.append(_("\n\n❌ Both Cartons & Boxes variance:\n• %s") % '\n• '.join([
                "%s (Cartons: %s→%s | Boxes: %s→%s)" % (l.product_id.name, l.cartons_to_be_produced, l.cartons_produced,
                                                        l.boxes_to_be_produced, l.boxes_produced) for l in both]))

        parts.append(
            _("\n\nPlease fill 'Reason of short / excess Quantity in FG' for the above line(s) before confirming."))
        return ''.join(parts)

    # ==========================================
    # INTERNAL TRANSFER (Production -> Store)
    # ==========================================
    def _get_transfer_locations(self):
        self.ensure_one()
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        source = self.env['stock.location'].search([
            ('name', '=', 'Production'), ('location_id', '=', warehouse.view_location_id.id),
            ('company_id', 'in', (self.env.company.id, False)),
        ], limit=1)
        if not source:
            source = self.env['stock.location'].search(
                [('usage', '=', 'production'), ('company_id', 'in', (self.env.company.id, False))], limit=1)
        dest = warehouse.lot_stock_id
        if not source or not dest:
            raise UserError(
                _("Could not determine the Production / Store locations. Please check warehouse configuration."))
        return warehouse, source, dest

    # def _complete_mo_for_production(self, line, production_location):
    #     self.ensure_one()
    #     so_name = self.sale_order_id.name
    #     mo = self.env['mrp.production'].search(
    #         [('origin', 'like', f'{so_name}%'), ('product_id', '=', line.product_id.id)], limit=1, order='id desc')
    #     if not mo: raise UserError(_("No Manufacturing Order found for %s (SO %s).") % (line.product_id.name, so_name))
    #     if mo.state == 'cancel': raise UserError(_("MO %s is CANCELLED.") % mo.name)
    #     if mo.state == 'done': return mo
    #
    #     if mo.state == 'draft': mo.action_confirm()
    #
    #     finished_move = mo.move_finished_ids.filtered(lambda m: m.product_id == line.product_id)[:1]
    #     if not finished_move: raise UserError(_("No finished-goods move found on MO %s.") % mo.name)
    #
    #     # CHANGED: register production output in PCS (cartons x pcs_per_carton)
    #     pcs = line.cartons_produced * (line.product_id.pcs_per_carton or 1.0)
    #     lot = mo.lot_producing_ids[:1] or line.lot_id
    #     finished_move.location_dest_id = production_location.id
    #     finished_move.quantity = pcs
    #
    #     if not finished_move.move_line_ids:
    #         self.env['stock.move.line'].create({
    #             'move_id': finished_move.id, 'product_id': finished_move.product_id.id,
    #             'lot_id': lot.id if lot else False, 'quantity': pcs,
    #             'product_uom_id': finished_move.product_uom.id, 'location_id': finished_move.location_id.id,
    #             'location_dest_id': production_location.id,
    #         })
    #     else:
    #         finished_move.move_line_ids.write({'lot_id': lot.id if lot else False, 'quantity': pcs,
    #                                            'location_dest_id': production_location.id})
    #
    #     res = mo.button_mark_done()
    #     for _attempt in range(5):
    #         if not isinstance(res, dict) or not res.get('res_model'): break
    #         model = res['res_model']
    #         ctx = res.get('context', {})
    #         wiz = self.env[model].with_context(**ctx).create({})
    #         if model == 'mrp.production.backorder':
    #             res = wiz.action_close_mo()
    #         elif model == 'mrp.immediate.production':
    #             res = wiz.process()
    #         elif model == 'mrp.consumption.warning':
    #             res = wiz.action_confirm()
    #         else:
    #             raise UserError(_("MO %s could not be completed automatically (wizard: %s).") % (mo.name, model))
    #
    #     if mo.state != 'done': raise UserError(_("MO %s is still '%s' after automatic completion.") % (mo.name, dict(
    #         mo._fields['state'].selection).get(mo.state)))
    #     return mo

    def _create_internal_transfer(self):
        self.ensure_one()
        if self.picking_id: return self.picking_id

        eligible_lines = self.line_ids.filtered(lambda l: l.product_id and l.cartons_produced > 0)
        if not eligible_lines:
            self.message_post(body=_("<b>Internal Transfer Skipped:</b> no produced quantity entered."))
            return self.env['stock.picking']

        warehouse, source, dest = self._get_transfer_locations()
        # for line in eligible_lines: self._complete_mo_for_production(line, source)

        # CHANGED: transfer quantity in PCS (cartons x pcs_per_carton)
        move_vals = [(0, 0, {
            'product_id': line.product_id.id,
            'product_uom_qty': line.cartons_produced * (line.product_id.pcs_per_carton or 1.0),
            'product_uom': line.product_id.uom_id.id, 'location_id': source.id, 'location_dest_id': dest.id,
        }) for line in eligible_lines]

        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.int_type_id.id, 'location_id': source.id,
            'location_dest_id': dest.id, 'origin': self.name, 'partner_id': self.sale_order_id.partner_id.id,
            'move_ids': move_vals,
        })
        self.picking_id = picking.id

        picking.action_assign()
        for move in picking.move_ids:
            rep_line = eligible_lines.filtered(lambda l: l.product_id == move.product_id)[:1]
            lot = rep_line.lot_id
            if not lot:
                mo = self.env['mrp.production'].search(
                    [('origin', 'like', f'{self.sale_order_id.name}%'), ('product_id', '=', move.product_id.id)],
                    limit=1, order='id desc')
                lot = mo.lot_producing_ids[:1]

            if not move.move_line_ids:
                self.env['stock.move.line'].create({
                    'move_id': move.id, 'product_id': move.product_id.id, 'lot_id': lot.id if lot else False,
                    'quantity': move.product_uom_qty, 'product_uom_id': move.product_uom.id,
                    'location_id': source.id, 'location_dest_id': dest.id,
                })
            else:
                move.move_line_ids.write({'lot_id': lot.id if lot else False, 'quantity': move.product_uom_qty})
            move.quantity = move.product_uom_qty

        self.message_post(body=_("<b>Internal Transfer Created:</b> %s. Please VALIDATE manually.") % picking.name)
        return picking

    def action_view_transfer(self):
        self.ensure_one()
        if not self.picking_id: raise UserError(_('No internal transfer generated yet.'))
        return {'type': 'ir.actions.act_window', 'name': _('Internal Transfer'), 'res_model': 'stock.picking',
                'res_id': self.picking_id.id, 'view_mode': 'form', 'target': 'current'}

    def action_draft(self):
        for rec in self:
            if rec.picking_id and rec.picking_id.state not in ('done', 'cancel'):
                rec.picking_id.action_cancel()
                rec.message_post(body=_("Linked transfer %s cancelled.") % rec.picking_id.name)
                rec.picking_id = False
            rec.write({'state': 'draft'})
            rec.message_post(body=_("FG Reporting re-opened to Draft."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('fg.reporting') or _('New')
        return super().create(vals_list)


class FgReportingLine(models.Model):
    _name = 'fg.reporting.line'
    _description = 'Finished Goods Reporting Line'
    _order = 'sno, id'

    reporting_id = fields.Many2one('fg.reporting', string='Report', required=True, ondelete='cascade')
    sno = fields.Integer(string='S #', readonly=True)
    product_id = fields.Many2one('product.product', string='Product Name', readonly=True)
    lot_id = fields.Many2one('stock.lot', string='Lot #', readonly=True)

    cartons_to_be_produced = fields.Float(string='No. of Cartons to be produced')
    boxes_to_be_produced = fields.Float(string='No. of Boxes to be produced')

    # EDITABLE COMPUTED FIELD: always mirrors boxes_produced / pcs_per_carton.
    # Typing boxes updates cartons live; typing cartons runs the inverse and updates boxes.
    cartons_produced = fields.Float(
        string='No. of Cartons produced',
        compute='_compute_cartons_produced',
        inverse='_inverse_cartons_produced',
        readonly=False,
        store=True,
    )
    boxes_produced = fields.Float(string='No. of Boxes produced')

    reason_short_excess = fields.Char(string='Reason of short / excess Quantity in FG')

    @api.depends('boxes_produced', 'product_id.pcs_per_carton')
    def _compute_cartons_produced(self):
        for line in self:
            pieces_per_carton = line.product_id.pcs_per_carton or 1.0
            line.cartons_produced = line.boxes_produced / pieces_per_carton

    def _inverse_cartons_produced(self):
        for line in self:
            pieces_per_carton = line.product_id.pcs_per_carton or 1.0
            line.boxes_produced = line.cartons_produced * pieces_per_carton

    @api.onchange('cartons_to_be_produced')
    def _onchange_cartons_to_be_produced(self):
        if self.cartons_to_be_produced:
            pieces_per_carton = self.product_id.pcs_per_carton or 1.0
            self.boxes_to_be_produced = self.cartons_to_be_produced * pieces_per_carton

    @api.onchange('boxes_to_be_produced')
    def _onchange_boxes_to_be_produced(self):
        if self.boxes_to_be_produced:
            pieces_per_carton = self.product_id.pcs_per_carton or 1.0
            self.cartons_to_be_produced = self.boxes_to_be_produced / pieces_per_carton
