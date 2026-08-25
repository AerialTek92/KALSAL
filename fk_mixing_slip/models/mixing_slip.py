from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


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

    customer_name = fields.Char(
        related='sale_order_id.partner_id.name',
        string='Customer Name', store=True)

    total_bags = fields.Integer(string='Total Bags')

    line_ids = fields.One2many('mixing.slip.line', 'slip_id', string='Materials')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Mixing Done'),
    ], string='Status', default='draft', tracking=True)


    # ---------- TOTALS FOR THE FOOTER ----------
    total_issued = fields.Float(string='Total Issued', compute='_compute_slip_totals')
    total_in_mixing = fields.Float(string='Total In Mixing', compute='_compute_slip_totals')
    total_wastage_kg = fields.Float(string='Total Wastage (kg)', compute='_compute_slip_totals')
    total_wastage_pct = fields.Float(string='Total Wastage %', compute='_compute_slip_totals')

    @api.depends('line_ids.quantity_issued', 'line_ids.in_mixing', 'line_ids.wastage_kg')
    def _compute_slip_totals(self):
        for rec in self:
            rec.total_issued = sum(rec.line_ids.mapped('quantity_issued'))
            rec.total_in_mixing = sum(rec.line_ids.mapped('in_mixing'))
            rec.total_wastage_kg = sum(rec.line_ids.mapped('wastage_kg'))
            rec.total_wastage_pct = round(
                (rec.total_wastage_kg / rec.total_issued) * 100.0, 2
            ) if rec.total_issued else 0.0

    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        for rec in self:
            rec.allowed_recipe_product_ids = \
                rec.sale_order_id.order_line.mapped('product_id')

    def _set_mrs_and_lines(self, mrs):
        """Attach the MRS and build one slip line per MRS line."""
        self.mrs_id = mrs
        self.line_ids = [
            (0, 0, {'mrs_line_id': line.id})
            for line in mrs.recipe_line_ids
        ]

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

    def action_mark_mixing_done(self):
        """Lock the slip and unlock the Line Clearance step."""
        for rec in self:
            if not rec.line_ids:
                raise UserError(_(
                    "Cannot mark mixing as done: no material lines on slip %s.")
                                % rec.name)
            if not rec.mrs_id:
                raise UserError(_(
                    "Cannot mark mixing as done: no source MRS linked to slip %s.")
                                % rec.name)

            # ==========================================
            # NEW VALIDATION: Block 0 or empty 'In Mixing' values
            # ==========================================
            zero_mixing_lines = rec.line_ids.filtered(lambda l: not l.in_mixing or l.in_mixing <= 0)
            if zero_mixing_lines:
                # Build a clean list of the products that are missing values
                missing_products = ', '.join([
                    l.item_description or l.product_id.display_name or _("Unknown Product")
                    for l in zero_mixing_lines
                ])
                raise UserError(_(
                    "Cannot mark mixing as done!\n\n"
                    "The following materials have 0 'In Mixing' quantity:\n"
                    "• %s\n\n"
                    "Please enter the actual quantity consumed for these items."
                ) % missing_products)
            # ==========================================

            rec.state = 'done'
            rec.message_post(body=_("Mixing Slip marked as DONE."))

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
    mrs_line_id = fields.Many2one(
        'material.requisition.line', string='MRS Line', readonly=True)

    sno = fields.Integer(related='mrs_line_id.sno', string='S.No', store=True)
    item_code = fields.Char(
        related='mrs_line_id.item_code', string='Item Code', store=True)
    item_description = fields.Char(
        related='mrs_line_id.item_description',
        string='Item Description', store=True)
    product_id = fields.Many2one(
        related='mrs_line_id.product_id', string='Item', store=True)
    uom_id = fields.Many2one(
        related='mrs_line_id.uom_id', string='UOM', store=True)
    quantity_issued = fields.Float(
        related='mrs_line_id.quantity_issued',
        string='Issued', store=True, readonly=True)

    in_mixing = fields.Float(string='In Mixing')

    wastage_kg = fields.Float(
        string='Wastage in kg',
        compute='_compute_wastage_kg',
        store=True,
        readonly=False,
    )
    wastage_pct = fields.Float(
        string='Wastage in %',
        compute='_compute_wastage_pct',
        store=True,
        readonly=False,
    )

    @api.onchange('in_mixing')
    def _onchange_in_mixing(self):
        for rec in self:
            if rec.in_mixing > rec.quantity_issued:
                raise UserError(_(
                    "In Mixing (%s) cannot exceed the Issued quantity (%s) for %s."
                ) % (rec.in_mixing, rec.quantity_issued,
                     rec.item_description or rec.product_id.display_name))

    @api.constrains('in_mixing', 'quantity_issued')
    def _check_in_mixing_within_issued(self):
        for rec in self:
            if rec.in_mixing > rec.quantity_issued:
                raise ValidationError(_(
                    "In Mixing (%s) cannot exceed the Issued quantity (%s) for %s."
                ) % (rec.in_mixing, rec.quantity_issued,
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
            if rec.quantity_issued:
                rec.wastage_pct = round(
                    (rec.wastage_kg / rec.quantity_issued) * 100.0, 2)
            else:
                rec.wastage_pct = 0.0
