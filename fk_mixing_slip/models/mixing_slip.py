from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup
from odoo.tools import float_compare


class MixingSlip(models.Model):
    _name = 'mixing.slip'
    _description = 'Mixing / Production Slip'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Slip No', required=True, copy=False,
                       readonly=True, default=lambda self: _('New'))
    date = fields.Date(string='Date', default=fields.Date.context_today)

    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', tracking=True,
        domain="[('stock_ready', '=', True)]",
        help="Dropdown of completed Sales Orders, same as MRS.")

    allowed_recipe_product_ids = fields.Many2many(
        'product.product', string='Allowed Recipes',
        compute='_compute_allowed_recipe_product_ids')

    recipe_product_id = fields.Many2one(
        'product.product', string='Recipe Name', tracking=True,
        domain="[('id', 'in', allowed_recipe_product_ids)]",
        help="Choose which product's recipe this slip is for.")

    mrs_id = fields.Many2one(
        'material.requisition.slip', string='Source MRS', readonly=True)

    mrp_production_id = fields.Many2one(
        'mrp.production', related='mrs_id.mrp_production_id',
        string='Manufacturing Order', tracking=True, copy=False)

    customer_name = fields.Char(
        related='sale_order_id.partner_id.name',
        string='Customer Name', store=True)

    # digits=(16, 4) so the exact value (e.g. 10.2) is DISPLAYED as stored,
    # not snapped to the default 2-dp display precision.
    total_bags = fields.Float(string='Total Bags')

    line_ids = fields.One2many(
        'mixing.slip.line', 'slip_id', string='Materials',
        domain=[('product_id.product_tmpl_id.product_type_custom', '!=', 'packaging')])

    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Mixing Done'),
    ], string='Status', default='draft', tracking=True)

    # ---------- TOTALS FOR THE FOOTER ----------
    total_issued = fields.Float(string='Total Issued', compute='_compute_slip_totals', digits=(16, 4))
    total_in_mixing = fields.Float(string='Total In Mixing', compute='_compute_slip_totals', digits=(16, 4))
    total_wastage_kg = fields.Float(string='Total Wastage (kg)', compute='_compute_slip_totals', digits=(16, 4))
    total_wastage_pct = fields.Float(string='Total Wastage %', compute='_compute_slip_totals', digits=(16, 4))
    wastage_remarks = fields.Text(
        string='Wastage Remarks (Required if >3%)',
        help="Explain why wastage exceeded 3%. Required to mark mixing as done.")

    # ---------- MRS BATCH QTY (readonly reference + cap for Total Bags) ----------
    max_total_bags = fields.Float(
        string='Max Total Bags (MRS)',
        related='mrs_id.qty_producing_with_wastage',
        digits=(16, 4),
        help="Live mirror of the source MRS's qty_producing_with_wastage. "
             "Total Bags may be lowered to what was physically achievable, "
             "but can never be raised above this.")

    @api.depends('mrs_id', 'mrs_id.qty_producing_with_wastage')
    def _compute_max_total_bags(self):
        for rec in self:
            rec.max_total_bags = rec.mrs_id.qty_producing_with_wastage or 0.0


    def _apply_total_bags_sync(self):
        """If Total Bags differs from the linked MO's Qty Producing, push
        Total Bags onto the MO — Mixing reflects what was PHYSICALLY
        achievable, so the MO's finished-goods target follows Mixing, not
        the other way around. Each line's 'In Mixing' is then reset to the
        MO's freshly recalculated per-line requirement for that batch size,
        which becomes the new floor those lines cannot be edited below."""
        for rec in self:
            mo = rec.mrp_production_id
            if not mo or not rec.total_bags:
                continue

            current_qty_producing = mo.qty_producing or 0.0
            if float_compare(float(rec.total_bags), current_qty_producing, precision_digits=4) != 0:
                mo.with_context(skip_mrs_sync=True).write({'qty_producing': float(rec.total_bags)})

            for line in rec.line_ids:
                if line.move_id:
                    # UNROUNDED: core's should_consume_qty is float_round'ed
                    # to the UoM rounding (1.0 on kg/Units snaps 2.55 -> 3).
                    line.in_mixing = line._get_unrounded_should_consume()

    @api.onchange('total_bags')
    def _onchange_total_bags(self):
        val  = self.total_bags
        for rec in self:
            if rec.max_total_bags and rec.total_bags:
                if rec.total_bags > rec.max_total_bags:
                    rec.total_bags = rec.max_total_bags
        self._apply_total_bags_sync()
        rec.total_bags = val

    def _set_mrs_and_lines(self, mrs):
        """Attach the MRS and build one slip line per MO Raw Material line.
        (This method was previously defined TWICE — the second definition
        silently overrode this one, so Total Bags was never auto-filled.
        Merged back into a single definition.)"""
        self.mrs_id = mrs

        # If the MRS doesn't have an MO generated yet, stop here.
        if not mrs.mrp_production_id:
            self.line_ids = [(5, 0, 0)]
            return

        # Fetch lines from the MO's raw material moves
        lines = []
        for i, move in enumerate(mrs.mrp_production_id.move_raw_ids, start=1):
            lines.append((0, 0, {
                'move_id': move.id,
                'sno': i,
            }))
        self.line_ids = lines

        # Default Total Bags to the MRS's wastage-adjusted batch quantity,
        # kept at FULL precision — no math.ceil, no rounding. The 2% buffer
        # (e.g. 10.2) stays 10.2, so no line's floor gets inflated.
        self.total_bags = mrs.qty_producing_with_wastage or 0.0
        self._apply_total_bags_sync()

    @api.depends('line_ids.quantity_issued', 'line_ids.in_mixing', 'line_ids.wastage_kg')
    def _compute_slip_totals(self):
        for rec in self:
            rec.total_issued = sum(rec.line_ids.mapped('quantity_issued'))
            rec.total_in_mixing = sum(rec.line_ids.mapped('in_mixing'))
            rec.total_wastage_kg = sum(rec.line_ids.mapped('wastage_kg'))
            # No round() — exact percentage. Side effect: the 3% gate in
            # action_mark_mixing_done is now evaluated on the true value.
            rec.total_wastage_pct = (
                (rec.total_wastage_kg / rec.total_issued) * 100.0
                if rec.total_issued else 0.0
            )

    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        for rec in self:
            rec.allowed_recipe_product_ids = \
                rec.sale_order_id.order_line.mapped('product_id')

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        self.line_ids = [(5, 0, 0)]
        self.mrs_id = False
        self.recipe_product_id = False
        if not self.sale_order_id:
            return

        mrs = self.env['material.requisition.slip'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('state', '!=', 'cancel'),
        ], order='id desc')

        if len(mrs) == 1:
            self.recipe_product_id = mrs.recipe_product_id
            self._set_mrs_and_lines(mrs)

    @api.onchange('recipe_product_id')
    def _onchange_recipe_product_id(self):
        self.line_ids = [(5, 0, 0)]
        self.mrs_id = False
        if not self.recipe_product_id or not self.sale_order_id:
            return

        mrs = self.env['material.requisition.slip'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('recipe_product_id', '=', self.recipe_product_id.id),
            ('state', '!=', 'cancel'),
        ], order='id desc', limit=1)

        if not mrs:
            return {'warning': {
                'title': _('No MRS Found'),
                'message': _(
                    'No MRS exists for %s on %s. '
                    'Please create the MRS first.') % (
                               self.recipe_product_id.display_name,
                               self.sale_order_id.name),
            }}

        self._set_mrs_and_lines(mrs)

    # ---------- SEQUENCE NUMBERING ----------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'mixing.slip') or _('New')
        return super().create(vals_list)

    # ---------- BACKTRACK: open the source MRS ----------
    def action_view_source_mrs(self):
        self.ensure_one()
        if not self.mrs_id:
            raise UserError(_('This slip is not linked to any MRS.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Source MRS'),
            'res_model': 'material.requisition.slip',
            'res_id': self.mrs_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

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
        Operations users — they inherit Quality access as part of their
        broader role, but shouldn't be pulled into department-level
        operational notifications."""
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

    def action_mark_mixing_done(self):
        """Lock the slip, produce the MO for the MRS's batch qty (creating a
        backorder for the rest), and unlock the Line Clearance step."""
        base_url = self._get_current_base_url()
        quality_partners = self._get_group_partners('fk_kalsal_security.group_management_quality')

        for rec in self:
            if not rec.line_ids:
                raise UserError(_(
                    "Cannot mark mixing as done: no material lines on slip %s.")
                                % rec.name)
            if not rec.mrs_id:
                raise UserError(_(
                    "Cannot mark mixing as done: no source MRS linked to slip %s.")
                                % rec.name)

            zero_mixing_lines = rec.line_ids.filtered(lambda l: not l.in_mixing or l.in_mixing <= 0)
            if zero_mixing_lines:
                missing_products = ', '.join([
                    l.item_description or l.product_id.display_name
                    for l in zero_mixing_lines
                ])
                raise UserError(_(
                    "Cannot mark mixing as done!\n\n"
                    "The following materials have 0 'In Mixing' quantity:\n"
                    "• %s\n\n"
                    "Please enter the actual quantity consumed for these items."
                ) % missing_products)

            if rec.total_wastage_pct > 3.0 and not rec.wastage_remarks:
                raise UserError(_(
                    "High Wastage Detected!\n\n"
                    "Total wastage is %.2f%% (threshold: 3%%).\n\n"
                    "Please provide remarks explaining the reason for high wastage "
                    "before marking mixing as done."
                ) % rec.total_wastage_pct)

            # ----------------------------------------------------------
            # PRODUCE PARTIAL: produce exactly what Mixing confirms was
            # physically achievable (Total Bags), with a backorder MO for
            # whatever remains against the original order quantity.
            # ----------------------------------------------------------
            if rec.mrs_id.mrp_production_id:
                mo = rec.mrs_id.mrp_production_id
                if mo.state not in ('done', 'cancel'):
                    rec._apply_total_bags_sync()  # safety net — see note below
                    rec._mo_produce_partial(mo, rec.total_bags)

            rec.mrs_id.action_done()
            rec.state = 'done'
            rec.message_post(body=_("Mixing Slip marked as DONE."))

            # ----------------------------------------------------------
            # Notification — purely informational from here on, doesn't
            # gate production anymore.
            # ----------------------------------------------------------
            sale_order = rec.sale_order_id or (
                rec.mrs_id.sale_order_id if hasattr(rec.mrs_id, 'sale_order_id') else False)
            sale_order_name = sale_order.name if sale_order else "Unknown SO"

            if quality_partners:
                if sale_order:
                    action = self.env['ir.actions.act_window'].sudo().create({
                        'name': _('New Line Clearance - %s') % sale_order_name,
                        'res_model': 'line.clearance',
                        'view_mode': 'form',
                        'target': 'current',
                        'context': "{'default_sale_order_id': %d}" % sale_order.id,
                    })
                else:
                    action = self.env.ref('am_kalsal_quality.action_line_clearance', raise_if_not_found=False)

                module_url = f"{base_url}/web#action={action.id}" if action else f"{base_url}/web"

                notification_msg = Markup(_(
                    "Mixing for Sale Order (<b>%s</b>) is done. "
                    "<a href='%s' target='_blank'>Click here</a> to create the Line Clearance sheet."
                )) % (sale_order_name, module_url)

                notify_ctx = dict(self.env.context, mail_notify_force_send=False)

                if hasattr(rec, 'message_notify'):
                    rec.with_context(notify_ctx).message_notify(
                        partner_ids=quality_partners.ids,
                        body=notification_msg,
                        subject=_("Mixing Completed: %s") % rec.name
                    )
                else:
                    self.env['mail.thread'].with_context(notify_ctx).message_notify(
                        model=rec._name,
                        res_id=rec.id,
                        partner_ids=quality_partners.ids,
                        body=notification_msg,
                        subject=_("Mixing Completed: %s") % rec.name
                    )

                for partner in quality_partners:
                    self.env['bus.bus']._sendone(
                        partner,
                        'simple_notification',
                        {
                            'type': 'success',
                            'title': _("Mixing Completed"),
                            'message': _("Mixing for SO %s is done. Check your inbox for the link.") % sale_order_name,
                            'sticky': True,
                        }
                    )

    def _mo_produce_partial(self, mo, qty_to_produce=None):
        """Produce `qty_to_produce` of `mo` and create a confirmed backorder
        for the remaining quantity (product_qty - produced)."""
        self.ensure_one()

        if mo.state == 'draft':
            mo.action_confirm()

        rounding = mo.product_uom_id.rounding

        if qty_to_produce is None:
            qty_to_produce = mo.qty_producing or mo.product_qty
        if float_compare(qty_to_produce, mo.product_qty, precision_rounding=rounding) > 0:
            qty_to_produce = mo.product_qty
        if float_compare(qty_to_produce, 0.0, precision_rounding=rounding) <= 0:
            return

        mo.write({'qty_producing': qty_to_produce})

        if mo.product_id.tracking == 'serial':
            if mo.lot_producing_ids and len(mo.lot_producing_ids) != qty_to_produce:
                mo.lot_producing_ids = [(5, 0, 0)]
            if not mo.lot_producing_ids:
                mo.action_generate_serial()
        elif mo.product_id.tracking == 'lot' and not mo.lot_producing_ids:
            mo.lot_producing_ids = [(0, 0, {
                'product_id': mo.product_id.id,
                'company_id': mo.company_id.id,
                'name': mo.name,
            })]

        # Force Odoo to distribute consumption across move_raw_ids for THIS
        # batch, exactly like the "Quantity Producing" onchange in the UI.
        # (See earlier comment — button_mark_done() skips _set_qty_producing()
        # when qty_producing is already set, leaving move quantities at 0.)
        mo.sudo()._set_qty_producing(pick_manual_consumption_moves=False)

        # Flexible consumption: actual usage is tracked on the Mixing Slip
        # lines, so don't fight Odoo's theoretical-vs-done warning.
        if 'consumption' in mo._fields and mo.consumption != 'flexible':
            mo.write({'consumption': 'flexible'})

        res = mo.button_mark_done()

        if isinstance(res, dict) and res.get('res_model') == 'mrp.production.backorder':
            wizard = self.env['mrp.production.backorder'].with_context(
                res.get('context') or {}
            ).create({'mrp_production_ids': [(6, 0, mo.ids)]})
            wizard.action_backorder()

    def action_reset_to_draft(self):
        """Allow corrections. (fk_line_clearance will add a guard once sheets exist.)"""
        for rec in self:
            rec.state = 'draft'
            rec.message_post(body=_("Mixing Slip re-opened to Draft."))


class MixingSlipLine(models.Model):
    _name = 'mixing.slip.line'
    _description = 'Mixing Slip Line'
    _order = 'sno, id'

    slip_id = fields.Many2one(
        'mixing.slip', string='Slip', required=True, ondelete='cascade')

    move_id = fields.Many2one(
        'stock.move', string='MO Raw Material Line', readonly=True)

    sno = fields.Integer(string='S.No', store=True)

    item_code = fields.Char(
        string='Item Code', compute='_compute_line_identity', store=True)
    item_description = fields.Char(
        string='Item Description', compute='_compute_line_identity', store=True)
    product_id = fields.Many2one(
        'product.product', string='Item',
        compute='_compute_line_identity', store=True)
    uom_id = fields.Many2one(
        'uom.uom', string='UOM', compute='_compute_line_identity', store=True)
    quantity_issued = fields.Float(
        string='Issued', compute='_compute_line_identity', store=True,
        help="What Store actually issued for this product on the linked "
             "MRS (including any wastage buffer they released) — NOT the "
             "MO's theoretical raw-material demand.", digits=(16, 4))

    def _get_unrounded_should_consume(self):
        """Core computes should_consume_qty as the proportional demand
            (qty_producing - qty_produced) * product_uom_qty / product_qty
        but then float_round()s it to the UOM ROUNDING FACTOR. With a UoM
        rounding of 1.0 (the default for Units / kg), 2.55 becomes 3 — this
        helper returns the same proportional demand WITHOUT any rounding so
        Mixing lines keep full decimal precision."""
        self.ensure_one()
        move = self.move_id
        if not move:
            return 0.0
        mo = move.raw_material_production_id
        if not mo or not mo.product_qty:
            return move.should_consume_qty or 0.0
        ratio = move.product_uom_qty / mo.product_qty
        return ((mo.qty_producing or 0.0) - (mo.qty_produced or 0.0)) * ratio

    @api.depends('move_id', 'move_id.product_id', 'move_id.product_uom',
                 'slip_id.mrs_id.recipe_line_ids.quantity_issued',
                 'slip_id.mrs_id.recipe_line_ids.product_id')
    def _compute_line_identity(self):
        for rec in self:
            m = rec.move_id
            if m:
                rec.product_id = m.product_id
                rec.item_code = m.product_id.default_code
                rec.item_description = m.product_id.display_name
                rec.uom_id = m.product_uom

                mrs_line = rec.slip_id.mrs_id.recipe_line_ids.filtered(
                    lambda l: l.product_id == m.product_id
                )[:1]

                if mrs_line:
                    rec.quantity_issued = mrs_line.quantity_issued
                else:
                    # Fallback: unrounded proportional demand, then the MO's
                    # full demand if the MO hasn't got a producing qty yet.
                    raw = rec._get_unrounded_should_consume()
                    rec.quantity_issued = raw if raw > 0 else m.product_uom_qty

                # Self-heal: quantity_issued can shrink if Store later edits
                # the MRS line's issued quantity downward.
                if rec.in_mixing and rec.quantity_issued and rec.in_mixing > rec.quantity_issued:
                    rec.in_mixing = rec.quantity_issued

    in_mixing = fields.Float(string='In Mixing', digits=(16, 4))

    wastage_kg = fields.Float(
        string='Wastage in kg',
        store=True,
        readonly=False, digits=(16, 4))

    wastage_pct = fields.Float(
        string='Wastage in %',
        compute='_compute_wastage_pct',
        store=True,
        readonly=False, digits=(16, 4))

    # Single definition (was duplicated — second silently overrode first).
    @api.onchange('in_mixing')
    def _onchange_in_mixing(self):
        if self.env.context.get('skip_in_mixing_floor_check'):
            return
        for rec in self:
            if rec.in_mixing > rec.quantity_issued:
                raise UserError(_(
                    "In Mixing (%s) cannot exceed the Issued quantity (%s) for %s."
                ) % (rec.in_mixing, rec.quantity_issued,
                     rec.item_description or rec.product_id.display_name))

            # UNROUNDED floor — must match what _apply_total_bags_sync writes
            # into in_mixing, otherwise the check compares 2.55 against 3.
            floor = rec._get_unrounded_should_consume()
            if floor and rec.in_mixing < floor:
                raise UserError(_(
                    "In Mixing (%s) cannot be less than %s for %s at the "
                    "current Total Bags (%s). To record less material used, "
                    "lower 'Total Bags' on the slip instead — that lowers "
                    "the Manufacturing Order's quantity and this floor "
                    "together."
                ) % (rec.in_mixing, floor,
                     rec.item_description or rec.product_id.display_name,
                     rec.slip_id.total_bags))

    @api.constrains('in_mixing', 'quantity_issued')
    def _check_in_mixing_within_issued(self):
        if self.env.context.get('skip_in_mixing_floor_check'):
            return
        for rec in self:
            # if rec.in_mixing > rec.quantity_issued:
            #     raise ValidationError(_(
            #         "In Mixing (%s) cannot exceed the Issued quantity (%s) for %s."
            #     ) % (rec.in_mixing, rec.quantity_issued,
            #          rec.item_description or rec.product_id.display_name))

            floor = rec._get_unrounded_should_consume()
            if floor and float_compare(rec.in_mixing, floor, precision_digits=4) < 0:
                raise ValidationError(_(
                    "In Mixing (%s) cannot be less than %s for %s at the "
                    "current Total Bags."
                ) % (rec.in_mixing, floor,
                     rec.item_description or rec.product_id.display_name))

    @api.depends('quantity_issued', 'in_mixing')
    def _compute_wastage_kg(self):
        for rec in self:
            if not rec.in_mixing:
                rec.wastage_kg = 0.0
            else:
                rec.wastage_kg = rec.quantity_issued - rec.in_mixing

    @api.depends('quantity_issued', 'wastage_kg')
    def _compute_wastage_pct(self):
        for rec in self:
            # No round() — full precision, matching the "don't round" rule.
            rec.wastage_pct = (
                (rec.wastage_kg / rec.quantity_issued) * 100.0
                if rec.quantity_issued else 0.0
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for line in records:
            move = line.move_id
            if not move:
                continue
            vals = {}

            raw_floor = line._get_unrounded_should_consume()
            issued_qty = raw_floor if raw_floor > 0 else move.product_uom_qty

            for fname, source in (
                    ('product_id', move.product_id.id),
                    ('item_code', move.product_id.default_code),
                    ('item_description', move.product_id.display_name),
                    ('uom_id', move.product_uom.id),
                    ('quantity_issued', issued_qty),
            ):
                field = self._fields.get(fname)
                if field and not field.readonly and not getattr(line, fname, None) and source:
                    vals[fname] = source
            if vals:
                line.write(vals)

            # Packaging rides along with the issue: pass-through consumption
            if (line.product_id.product_type_custom == 'packaging'
                    and 'in_mixing' in self._fields and not line.in_mixing):
                line.in_mixing = line.quantity_issued
        return records