from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class TecoProduction(models.Model):
    _name = 'teco.production'
    _description = 'TECO Production Form'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: 'New')

    # MAIN FIELDS
    sale_order_id = fields.Many2one('sale.order', string='SO Number', tracking=True)
    product_id = fields.Many2one('product.product', string='Product')

    available_product_ids = fields.Many2many(
        'product.product',
        compute='_compute_available_product_ids'
    )

    # ONE2MANY LINE IDS FOR EVERY TAB
    reel_consumption_line_ids = fields.One2many('teco.reel.consumption', 'teco_id', string='Reel Consumption')
    material_issuance_line_ids = fields.One2many('teco.material.issuance', 'teco_id', string='Material Issuance')
    box_usage_line_ids = fields.One2many('teco.box.usage', 'teco_id', string='Box Usage')
    gum_issuance_line_ids = fields.One2many('teco.gum.issuance', 'teco_id', string='Gum Issuance')
    production_line_ids = fields.One2many('teco.production.line', 'teco_id', string='Production in Units')
    labor_line_ids = fields.One2many('teco.labor.line', 'teco_id', string='Men Power Used')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('teco.production') or 'New'
        return super().create(vals_list)

    @api.depends('sale_order_id')
    def _compute_available_product_ids(self):
        for rec in self:
            if rec.sale_order_id:
                so_product_ids = rec.sale_order_id.order_line.product_id.ids
                domain = [
                    ('sale_order_id', '=', rec.sale_order_id.id),
                    ('product_id', 'in', so_product_ids),
                ]
                if rec._origin.id:
                    domain.append(('id', '!=', rec._origin.id))
                existing_tecos = self.env['teco.production'].search(domain)
                used_product_ids = existing_tecos.mapped('product_id').ids
                available_ids = [pid for pid in so_product_ids if pid not in used_product_ids]
                rec.available_product_ids = available_ids
            else:
                rec.available_product_ids = False

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        self.product_id = False

    @api.onchange('product_id')
    def _onchange_product_id(self):
        # 1. ALWAYS clear all existing lines when the product changes
        self.reel_consumption_line_ids = [(5, 0, 0)]
        self.material_issuance_line_ids = [(5, 0, 0)]
        self.box_usage_line_ids = [(5, 0, 0)]
        self.gum_issuance_line_ids = [(5, 0, 0)]
        self.production_line_ids = [(5, 0, 0)]
        self.labor_line_ids = [(5, 0, 0)]

        # 2. If a new product is selected, add a single null value line to every tab
        if self.product_id:
            self.reel_consumption_line_ids = [(0, 0, {})]
            self.material_issuance_line_ids = [(0, 0, {})]
            self.box_usage_line_ids = [(0, 0, {})]
            self.gum_issuance_line_ids = [(0, 0, {})]
            self.production_line_ids = [(0, 0, {})]
            self.labor_line_ids = [(0, 0, {})]

# ==========================================
# LINE MODELS - Each contains ALL Excel fields
# ==========================================

class TecoReelConsumption(models.Model):
    _name = 'teco.reel.consumption'
    _description = 'Reel Consumption Line'

    teco_id = fields.Many2one('teco.production', string='TECO Reference', ondelete='cascade')
    total_qty = fields.Float(string='Total Qty')
    order_qty_required_kgs = fields.Float(string='Order qty Required (in kgs)',compute='_compute_order_qty_required_kgs', store=True)
    total_reel_used_kgs = fields.Float(string='Total Reel Used (in kgs)')

    total_pack_produced = fields.Float(
        string='Total pack produced',
        store=True,
        # readonly=True,
        # digits=(16, 4)
    )
    scrap_percentage = fields.Float(
        string='Scrap %',
        compute='_compute_scrap_percentage',
        store=True,
        readonly=True,
        digits=(16, 4)

    )
    reason_high_scrap = fields.Text(string='Reason of high scrap (if observed)')

    @api.depends('total_qty')
    def _compute_order_qty_required_kgs(self):
        for rec in self:
            if rec.total_qty:
                rec.order_qty_required_kgs = ((rec.total_qty * 2.1) / 1000)
            else:
                rec.total_pack_produced = 0.0

    @api.depends('total_reel_used_kgs','total_pack_produced')
    def _compute_scrap_percentage(self):
        for rec in self:
            if rec.total_pack_produced:
                pack_prod = ((rec.total_pack_produced * 2.1) / 1000)
                if rec.total_reel_used_kgs:
                    rec.scrap_percentage = (pack_prod - rec.total_reel_used_kgs) / rec.total_reel_used_kgs
            else:
                rec.scrap_percentage = 0.0

    @api.constrains('scrap_percentage', 'reason_high_scrap')
    def _check_high_scrap_reason(self):
        for rec in self:
            # 0.02 is 2% (because percentage widget uses decimals)
            if rec.scrap_percentage > 0.02 and not rec.reason_high_scrap:
                raise ValidationError(_("Reason of high scrap is required when Scrap exceeds 2%."))


class TecoMaterialIssuance(models.Model):
    _name = 'teco.material.issuance'
    _description = 'Material Issuance Line'

    teco_id = fields.Many2one('teco.production', string='TECO Reference', ondelete='cascade')
    order_qty_required_kgs = fields.Float(string='Order qty Required (in kgs)')
    material_issue = fields.Float(string='Material issued quantity')
    total_produced_qty = fields.Float(string='Total produced quantity')

    scrap_percentage = fields.Float(
        string='Scrap %',
        compute='_compute_scrap_percentage',
        store=True,
        readonly=True,
        digits=(16, 4)

    )
    reason_high_scrap = fields.Text(string='Reason of high scrap (if observed)')

    @api.depends('total_produced_qty', 'order_qty_required_kgs')
    def _compute_scrap_percentage(self):
        for rec in self:
            if rec.total_produced_qty and rec.total_produced_qty != 0:
                rec.scrap_percentage = (rec.order_qty_required_kgs - rec.total_produced_qty) / rec.total_produced_qty
            else:
                rec.scrap_percentage = 0.0

    @api.constrains('scrap_percentage', 'reason_high_scrap')
    def _check_high_scrap_reason(self):
        for rec in self:
            if rec.scrap_percentage > 0.02 and not rec.reason_high_scrap:
                raise ValidationError(_("Reason of high scrap is required when Scrap exceeds 2%."))


class TecoBoxUsage(models.Model):
    _name = 'teco.box.usage'
    _description = 'Box Usage Line'

    teco_id = fields.Many2one('teco.production', string='TECO Reference', ondelete='cascade')
    total_qty = fields.Float(string='Total Qty' )
    order_qty_required = fields.Float(string='Order qty Required (boxes UNIT)', compute='_compute_order_qty_required_kgs', store=True)
    total_box_used = fields.Float(string='Total Box Used (in order)')
    total_pack_produced = fields.Float(string='Total pack produced')

    scrap_percentage = fields.Float(
        string='Scrap %',
        compute='_compute_scrap_percentage',
        store=True,
        readonly=True,
        digits = (16, 4)

    )
    reason_high_scrap = fields.Text(string='Reason of high scrap (if observed)')


    @api.depends('total_qty')
    def _compute_order_qty_required_kgs(self):
        for rec in self:
            if rec.total_qty:
                rec.order_qty_required = rec.total_qty * 0.3
            else:
                rec.order_qty_required = 0.0

    @api.depends('total_box_used', 'total_pack_produced')
    def _compute_scrap_percentage(self):
        for rec in self:
            if rec.order_qty_required and rec.total_box_used and rec.total_pack_produced:
                rec.scrap_percentage = (rec.total_box_used - rec.total_pack_produced) / rec.total_box_used
            else:
                rec.scrap_percentage = 0.0

    @api.constrains('scrap_percentage', 'reason_high_scrap')
    def _check_high_scrap_reason(self):
        for rec in self:
            if rec.scrap_percentage > 0.02 and not rec.reason_high_scrap:
                raise ValidationError(_("Reason of high scrap is required when Scrap exceeds 2%."))


class TecoGumIssuance(models.Model):
    _name = 'teco.gum.issuance'
    _description = 'Gum Issuance Line'

    teco_id = fields.Many2one('teco.production', string='TECO Reference', ondelete='cascade')
    ttl_boxes_req = fields.Float(string='Total BOXES Required')
    total_gum_issued = fields.Float(string='Total gum issued')
    gum_used = fields.Float(string='Total gum used')

    scrap_percentage = fields.Float(
        string='Scrap %',
        compute='_compute_scrap_percentage',
        store=True,
        readonly=True,
        digits=(16, 4)

    )
    reason_high_scrap = fields.Text(string='Reason of high scrap (if observed)')

    @api.depends('total_gum_issued', 'ttl_boxes_req','gum_used')
    def _compute_scrap_percentage(self):
        for rec in self:
            if rec.ttl_boxes_req and rec.gum_used and rec.total_gum_issued:
                rec.scrap_percentage = (rec.gum_used - rec.ttl_boxes_req) / rec.ttl_boxes_req
            else:
                rec.scrap_percentage = 0.0

    @api.constrains('scrap_percentage', 'reason_high_scrap')
    def _check_high_scrap_reason(self):
        for rec in self:
            if rec.scrap_percentage > 0.02 and not rec.reason_high_scrap:
                raise ValidationError(_("Reason of high scrap is required when Scrap exceeds 2%."))


class TecoProductionLine(models.Model):
    _name = 'teco.production.line'
    _description = 'Production Line'

    teco_id = fields.Many2one('teco.production', string='TECO Reference', ondelete='cascade')
    prod_in_units = fields.Float(string='Total Production in units')
    total_capacity = fields.Float(string='Total capacity')

    global_efficiency = fields.Float(
        string='G.E %',
        compute='_compute_global_efficiency',
        store=True,
        readonly=True,
        digits=(16, 4)
    )
    reason_low_prod = fields.Text(string='Reason of low production (if observed)')

    @api.depends('prod_in_units', 'total_capacity')
    def _compute_global_efficiency(self):
        for rec in self:
            if rec.total_capacity and rec.total_capacity != 0:
                rec.global_efficiency = rec.prod_in_units / rec.total_capacity
            else:
                rec.global_efficiency = 0.0


class TecoLaborLine(models.Model):
    _name = 'teco.labor.line'
    _description = 'Men Power Used Line'

    teco_id = fields.Many2one('teco.production', string='TECO Reference', ondelete='cascade')

    approved_men_power = fields.Float(
        string='Approve men power',
        compute='_compute_approved_men_power',
        store=True,
        readonly=True
    )
    used_men_power = fields.Float(string='Men power used')
    days_used = fields.Float(string='No. of days')

    percent_extra_wrk = fields.Float(
        string='PERCENTAGE of Extra Work (if observed)',
        compute='_compute_percent_extra_wrk',
        store=True,
        readonly=True,
        digits=(16, 4)

    )
    high_labor_cost = fields.Text(string='Reason of extra Labor used (if observed)')

    @api.depends('teco_id', 'teco_id.box_usage_line_ids.order_qty_required')
    def _compute_approved_men_power(self):
        for rec in self:
            total_box_req = sum(rec.teco_id.box_usage_line_ids.mapped('total_qty')) if rec.teco_id else 0.0
            if total_box_req and total_box_req != 0:
                rec.approved_men_power = total_box_req / 525
            else:
                rec.approved_men_power = 0.0

    @api.depends('used_men_power', 'approved_men_power', 'days_used')
    def _compute_percent_extra_wrk(self):
        for rec in self:
            if rec.approved_men_power > 0 and rec.days_used > 0:
                rec.percent_extra_wrk = ((rec.used_men_power * rec.days_used) - rec.approved_men_power) / rec.approved_men_power
            else:
                rec.percent_extra_wrk = 0.0