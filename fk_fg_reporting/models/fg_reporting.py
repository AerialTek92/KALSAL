from odoo import models, fields, api, _
from odoo.exceptions import UserError


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

    line_ids = fields.One2many(
        'fg.reporting.line', 'reporting_id', string='Finished Goods Lines')

    # ==========================================
    # GATING: SOs with AT LEAST ONE passed Semi-Finished QC
    # ==========================================
    def _compute_allowed_sale_order_ids(self):
        """Allow Sale Orders where AT LEAST ONE product has passed Semi-Finished QC."""
        for rec in self:
            # 1. Fetch all passed Semi-Finished QCs
            passed_sfg = self.env['semi.finished.qc'].search([('state', '=', 'passed')])

            so_passed_products = {}
            for qc in passed_sfg:
                if qc.sale_order_id:
                    so_passed_products.setdefault(qc.sale_order_id.id, set()).add(qc.product_id.id)

            allowed_sos = self.env['sale.order']

            # 2. Allow the SO if it has at least one passed product (No longer requires ALL products)
            for so_id in so_passed_products.keys():
                allowed_sos |= self.env['sale.order'].browse(so_id)

            rec.allowed_sale_order_ids = allowed_sos

    # ==========================================
    # ONCHANGE: auto-build lines ONLY for passed products
    # ==========================================
    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        """Auto-build lines ONLY for products that have PASSED Semi-Finished QC."""
        # 1. Clear existing lines
        self.line_ids = [(5, 0, 0)]
        if not self.sale_order_id:
            return

        # 2. Find all products for this SO that have PASSED Semi-Finished QC
        passed_products = self.env['semi.finished.qc'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('state', '=', 'passed'),
        ]).mapped('product_id')

        # 3. Filter the SO lines to ONLY include those passed products
        # (This is where Products B and C get excluded if they haven't passed yet)
        eligible_so_lines = self.sale_order_id.order_line.filtered(
            lambda l: l.product_id in passed_products)

        if not eligible_so_lines:
            return {'warning': {
                'title': _('No Eligible Products'),
                'message': _(
                    'None of the products on Sale Order %s have passed '
                    'Semi-Finished QC yet. Please complete QC before reporting.'
                ) % self.sale_order_id.name,
            }}

        # 4. Auto-build lines ONLY for the eligible (passed) products
        lines = []
        sno = 1

        for so_line in eligible_so_lines:
            product = so_line.product_id

            # Calculate quantities (Cartons = SO Qty, Boxes = Cartons * 144)
            cartons_to_be = int(so_line.product_uom_qty)
            boxes_to_be = cartons_to_be * 144

            lines.append((0, 0, {
                'sno': sno,
                'product_id': product.id,
                'lot_id': self._fetch_lot_for_product(product).id or False,
                'cartons_to_be_produced': cartons_to_be,
                'boxes_to_be_produced': boxes_to_be,
            }))
            sno += 1

        if lines:
            self.line_ids = lines

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

    # ==========================================
    # ACTIONS & VALIDATION
    # ==========================================
    def action_confirm(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("No finished goods lines found. Please select a Sale Order first."))

            # VARIANCE RULE: produced != to-be-produced -> reason is mandatory
            no_reason = rec.line_ids.filtered(
                lambda l: (l.cartons_produced != l.cartons_to_be_produced
                           or l.boxes_produced != l.boxes_to_be_produced)
                          and not l.reason_short_excess)
            if no_reason:
                raise UserError(rec._build_variance_error(no_reason))

            rec.write({'state': 'confirmed'})
            rec.message_post(body=_("<b>Finished Goods Reporting Confirmed.</b>"))
            rec._create_internal_transfer()

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

    def _complete_mo_for_production(self, line, production_location):
        self.ensure_one()
        so_name = self.sale_order_id.name
        mo = self.env['mrp.production'].search(
            [('origin', 'like', f'{so_name}%'), ('product_id', '=', line.product_id.id)], limit=1, order='id desc')
        if not mo: raise UserError(_("No Manufacturing Order found for %s (SO %s).") % (line.product_id.name, so_name))
        if mo.state == 'cancel': raise UserError(_("MO %s is CANCELLED.") % mo.name)
        if mo.state == 'done': return mo

        if mo.state == 'draft': mo.action_confirm()

        finished_move = mo.move_finished_ids.filtered(lambda m: m.product_id == line.product_id)[:1]
        if not finished_move: raise UserError(_("No finished-goods move found on MO %s.") % mo.name)

        lot = mo.lot_producing_ids[:1] or line.lot_id
        finished_move.location_dest_id = production_location.id
        finished_move.quantity = line.cartons_produced

        if not finished_move.move_line_ids:
            self.env['stock.move.line'].create({
                'move_id': finished_move.id, 'product_id': finished_move.product_id.id,
                'lot_id': lot.id if lot else False, 'quantity': line.cartons_produced,
                'product_uom_id': finished_move.product_uom.id, 'location_id': finished_move.location_id.id,
                'location_dest_id': production_location.id,
            })
        else:
            finished_move.move_line_ids.write({'lot_id': lot.id if lot else False, 'quantity': line.cartons_produced,
                                               'location_dest_id': production_location.id})

        res = mo.button_mark_done()
        for _attempt in range(5):
            if not isinstance(res, dict) or not res.get('res_model'): break
            model = res['res_model']
            ctx = res.get('context', {})
            wiz = self.env[model].with_context(**ctx).create({})
            if model == 'mrp.production.backorder':
                res = wiz.action_close_mo()
            elif model == 'mrp.immediate.production':
                res = wiz.process()
            elif model == 'mrp.consumption.warning':
                res = wiz.action_confirm()
            else:
                raise UserError(_("MO %s could not be completed automatically (wizard: %s).") % (mo.name, model))

        if mo.state != 'done': raise UserError(_("MO %s is still '%s' after automatic completion.") % (mo.name, dict(
            mo._fields['state'].selection).get(mo.state)))
        return mo

    def _create_internal_transfer(self):
        self.ensure_one()
        if self.picking_id: return self.picking_id

        eligible_lines = self.line_ids.filtered(lambda l: l.product_id and l.cartons_produced > 0)
        if not eligible_lines:
            self.message_post(body=_("<b>Internal Transfer Skipped:</b> no produced quantity entered."))
            return self.env['stock.picking']

        warehouse, source, dest = self._get_transfer_locations()
        for line in eligible_lines: self._complete_mo_for_production(line, source)

        move_vals = [(0, 0, {
            'product_id': line.product_id.id, 'product_uom_qty': line.cartons_produced,
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
    cartons_produced = fields.Float(string='No. of Cartons produced')
    boxes_to_be_produced = fields.Float(string='No. of Boxes to be produced')
    boxes_produced = fields.Float(string='No. of Boxes produced')
    reason_short_excess = fields.Char(string='Reason of short / excess Quantity in FG')

    @api.onchange('cartons_to_be_produced')
    def _onchange_cartons_to_be_produced(self):
        """Keep Boxes = Cartons × 144 in sync."""
        if self.cartons_to_be_produced:
            self.boxes_to_be_produced = self.cartons_to_be_produced * 144