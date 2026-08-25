from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime

class LineClearance(models.Model):
    _name = 'line.clearance'
    _description = 'Line Clearance Check Sheet'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: 'New')

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        required=True,
        domain="[('mixing_doc.state', '=', 'done')]"  # 👈 CHANGED: Only show SOs with completed mixing
    )

    mixing_slip_id = fields.Many2one('mixing.slip', string='Mixing Slip')

    issue_date = fields.Date(string='Issue Date', default=fields.Date.context_today, readonly=True)
    revision = fields.Char(string='Rev', default='00', readonly=True)

    product_id = fields.Many2one('product.product', string='Product Name', required=True)

    previous_product_id = fields.Many2one('product.product', string='Previous Product')
    previous_product_removed = fields.Selection([('yes', 'Yes'), ('no', 'No')], string='Previous Product Removed',
                                                default='no')

    mo_id = fields.Many2one('mrp.production', string='Manufacturing Order', readonly=True)

    batch_no = fields.Many2one('stock.lot', string='Batch No')

    machine_line = fields.Char(string='Machine/ Line')
    date = fields.Date(string='Date', default=fields.Date.context_today, required=False)

    time = fields.Char(
        string='Time',
        required=False,
        default=lambda self: self._get_default_current_time()
    )

    user_id = fields.Many2one('res.users', string='Inspector', default=lambda self: self.env.user)

    personal_hygiene_ok = fields.Selection([('yes', 'Yes'), ('no', 'No')], string='Personal Hygiene', default='no')
    accessories_ok = fields.Selection([('yes', 'Yes'), ('no', 'No')], string='Accessories', default='no')
    ppe_ok = fields.Selection([('comply', 'Comply'), ('not_comply', 'Not Comply')],
                              string='PPEs (Gloves, Hairnet, Safety Shoes)', default='comply')

    direct_contact_raw_material_ok = fields.Selection([('yes', 'Yes'), ('no', 'No')],
                                                      string='Direct Contact Raw Material Cover', default='no')
    sealer_properly_clean_ok = fields.Selection([('yes', 'Yes'), ('no', 'No')], string='Sealer Properly Clean',
                                                default='no')
    utensil_clean = fields.Selection([('yes', 'Yes'), ('no', 'No')], string='Utensil Clean', default='no')

    remarks = fields.Text(string='Remarks / Observations')

    operator_signature = fields.Binary(string='Operator Signature')
    supervisor_signature = fields.Binary(string='Supervisor Signature')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed')
    ], default='draft', string='Status', tracking=True)

    so_product_ids = fields.Many2many('product.product', compute='_compute_so_product_ids', string='SO Products')

    @api.model
    def _get_default_current_time(self):
        """Return current time as a 12-hour format string (e.g., 02:30 PM)."""
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        return now.strftime('%I:%M %p')

    @api.depends('sale_order_id')
    def _compute_so_product_ids(self):
        for rec in self:
            if rec.sale_order_id:
                # 👈 CHANGED: Fetch products ONLY from mixing slips marked as 'done'
                done_slips = self.env['mixing.slip'].search([
                    ('sale_order_id', '=', rec.sale_order_id.id),
                    ('state', '=', 'done')
                ])
                rec.so_product_ids = done_slips.mapped('recipe_product_id')
            else:
                rec.so_product_ids = False

    @api.onchange('product_id')
    def _onchange_product_id_fetch_batch(self):
        for rec in self:
            batch = False
            if rec.sale_order_id and rec.product_id:
                mo = self.env['mrp.production'].search([
                    ('origin', '=', rec.sale_order_id.name),
                    ('product_id', '=', rec.product_id.id)
                ], limit=1)
                if mo:
                    batch = mo.lot_producing_ids.id

            rec.batch_no = batch

    @api.constrains('time')
    def _check_time_range(self):
        for record in self:
            if record.time:
                try:
                    parsed_time = datetime.strptime(record.time.upper(), '%I:%M %p')
                except (ValueError, TypeError):
                    raise UserError(_("Time must be in a 12-hour format like '02:30 PM'."))

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        self.product_id = False
        self.batch_no = False
        self.mo_id = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('line.clearance') or _('New')

            if vals.get('time'):
                try:
                    parsed = datetime.strptime(vals['time'].upper(), '%I:%M %p')
                    vals['time'] = parsed.strftime('%I:%M %p')
                except ValueError:
                    pass

        return super().create(vals_list)

    def write(self, vals):
        if vals.get('time'):
            try:
                parsed = datetime.strptime(vals['time'].upper(), '%I:%M %p')
                vals['time'] = parsed.strftime('%I:%M %p')
            except ValueError:
                pass
        return super().write(vals)

    def action_confirm(self):
        if not self.machine_line:
            raise UserError("Machine Line is required for Confirming Line Clearance.")
        self.write({'state': 'confirmed'})

    def action_draft(self):
        self.write({'state': 'draft'})

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    _description = 'Sale Order'

    mixing_doc = fields.One2many('mixing.slip', 'sale_order_id', string='Mixing Document')

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    _description = 'Sale Order Line'

    mixing_doc = fields.One2many(
        'mixing.slip', 'sale_order_line_id',
        string='Mixing Documents'
    )

    mixing_slip_ids = fields.Many2many(
        'mixing.slip',
        string='All SO Mixing Slips',
        compute='_compute_mixing_slip_ids',
    )

    @api.depends('order_id', 'product_id')
    def _compute_mixing_slip_ids(self):
        for rec in self:
            slips = self.env['mixing.slip'].search([
                ('sale_order_id', '=', rec.order_id.id),
                ('recipe_product_id', '=', rec.product_id.id),
            ])
            rec.mixing_slip_ids = slips

class MixingSlip(models.Model):
    _inherit = 'mixing.slip'

    sale_order_line_id = fields.Many2one(
        'sale.order.line', string='SO Line',
        help="Auto-filled from the SO line whose product matches the recipe product.",
        tracking=True, ondelete='restrict')

    def _resolve_sale_order_line(self):
        """Find the SO line matching sale_order_id + recipe_product_id."""
        self.ensure_one()
        if not self.sale_order_id or not self.recipe_product_id:
            return False
        return self.env['sale.order.line'].search([
            ('order_id', '=', self.sale_order_id.id),
            ('product_id', '=', self.recipe_product_id.id),
        ], order='id asc', limit=1)

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        self.line_ids = [(5, 0, 0)]
        self.mrs_id = False
        self.recipe_product_id = False
        self.sale_order_line_id = False
        if not self.sale_order_id:
            return

        mrs = self.env['material.requisition.slip'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('state', '!=', 'cancel'),
        ], order='id desc')

        if len(mrs) == 1:
            self.recipe_product_id = mrs.recipe_product_id
            self._set_mrs_and_lines(mrs)
            self.sale_order_line_id = self._resolve_sale_order_line()

    @api.onchange('recipe_product_id')
    def _onchange_recipe_product_id(self):
        self.line_ids = [(5, 0, 0)]
        self.mrs_id = False
        # NEW: resolve SO line as soon as the recipe product is picked
        self.sale_order_line_id = self._resolve_sale_order_line()

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

            # NEW: back-fill sale_order_line_id if it wasn't provided
            if not vals.get('sale_order_line_id'):
                so_id = vals.get('sale_order_id')
                # recipe_product_id might be set directly or via onchange
                recipe_id = vals.get('recipe_product_id')
                if so_id and recipe_id:
                    line = self.env['sale.order.line'].search([
                        ('order_id', '=', so_id),
                        ('product_id', '=', recipe_id),
                    ], order='id asc', limit=1)
                    if line:
                        vals['sale_order_line_id'] = line.id
        return super().create(vals_list)

    def action_mark_mixing_done(self):
        """Mark mixing as done and notify quality users via popup notification."""
        res = super().action_mark_mixing_done()

        quality_group = self.env.ref('am_kalsal_quality.group_quality_user', raise_if_not_found=False)
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        # Get the Action ID for Line Clearance to create a direct link.
        # IMPORTANT: Replace 'your_module.action_line_clearance' with the actual XML ID
        # of your Line Clearance window action (menu action).
        lc_action = self.env.ref('your_module.action_line_clearance', raise_if_not_found=False)
        action_param = f"action={lc_action.id}&" if lc_action else ""

        for rec in self:
            sale_order = rec.sale_order_id or (
                rec.mrs_id.sale_order_id if hasattr(rec.mrs_id, 'sale_order_id') else False)
            sale_order_name = sale_order.name if sale_order else "Unknown SO"

            # Create the URL that opens the Line Clearance module (list view)
            module_url = f"{base_url}/web#{action_param}model=line.clearance&view_type=list"

            if quality_group:
                quality_users = self.env['res.users'].search([('group_ids', 'in', [quality_group.id])])

                # HTML formatted message body containing a clickable link to your target Odoo URL
                notification_msg = _(
                    "Mixing for Sale Order (<b>%s</b>) is done. <a href='%s' target='_blank'>Click here</a> to open the Line Clearance module."
                ) % (sale_order_name, module_url)

                for user in quality_users:
                    # 1. Create a persistent system message record in Odoo's mail system
                    message = self.env['mail.message'].create({
                        'subject': _("Mixing Completed: %s") % rec.name,
                        'body': notification_msg,
                        'message_type': 'user_notification',
                        'subtype_id': self.env.ref('mail.mt_note').id,
                        'model': rec._name,
                        'res_id': rec.id,
                    })

                    # 2. Bind the message directly to the target user's unread notification inbox table
                    self.env['mail.notification'].create({
                        'mail_message_id': message.id,
                        'res_partner_id': user.partner_id.id,
                        'notification_type': 'inbox',  # Forces it into the sidebar panel
                        'notification_status': 'sent',
                        'is_read': False,             # Appears as unread/new
                    })

                    # 3. Live-refresh the Discuss channel so the sidebar badge increments instantly
                    self.env['bus.bus']._sendone(
                        user.partner_id,
                        'mail.record/insert',
                        {'Notification': [{'id': message.id, 'is_read': False}]}
                    )

            rec.state = 'done'

            if self.sale_order_id and self.recipe_product_id:
                mo = self.env['mrp.production'].search([
                    ('origin', '=', self.sale_order_id.name),
                    ('product_id', '=', self.recipe_product_id.id)
                ], limit=1)

                if not mo.lot_producing_ids:
                    mo.action_generate_serial()

        return res

