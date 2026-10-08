from odoo import models, fields, api, _
from odoo.exceptions import UserError


class SwabTestingReport(models.Model):
    _name = 'swab.testing.report'
    _description = 'Swab Testing Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Report No', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True)

    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, required=True)

    effective_date = fields.Date(
        string='Effective Date', default=fields.Date.context_today,
        help="Defaults to the day the report is created; editable manually.")

    # ==========================================
    # SIMPLE WORKFLOW: Draft -> Passed / Failed
    # ==========================================
    state = fields.Selection([
        ('draft', 'Draft'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', tracking=True)

    # ==========================================
    # SAMPLING INFORMATION (header)
    # ==========================================
    sampling_information = fields.Char(
        string='Sampling Information (if any)', default='Machine Swab')
    temperature = fields.Float(string='Temp (°C)', default=24.0)
    sample_condition = fields.Selection([
        ('satisfactory', 'Satisfactory'),
        ('unsatisfactory', 'Unsatisfactory'),
    ], string='Sample Condition', default='satisfactory')

    # ==========================================
    # CONCLUSION SECTION (fields under the heading)
    # ==========================================
    deviation_from_methods = fields.Char(
        string='Deviation from Testing / Sampling Methods', default='No')
    measurement_uncertainty = fields.Char(
        string='Result of Measurement Uncertainty (if any)', default='N/R')
    interpretation_additional = fields.Char(
        string='Interpretation of Test Result / Additional Information',
        default='NA')

    line_ids = fields.One2many('swab.testing.line', 'report_id', string='Samples')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'swab.testing.report') or _('New')
        return super().create(vals_list)

    # ==========================================
    # WORKFLOW ACTIONS
    # ==========================================
    def _check_ready_for_decision(self):
        """Light gates: at least one sample line, every line has a product."""
        for rec in self:
            if not rec.line_ids:
                raise UserError(_(
                    "Add at least one sample line before passing or failing the test."))
            if rec.line_ids.filtered(lambda l: not l.product_id):
                raise UserError(_(
                    "Please select a Product on every sample line "
                    "before deciding the result."))

    def action_pass(self):
        self._check_ready_for_decision()
        self.write({'state': 'passed'})

    def action_fail(self):
        self._check_ready_for_decision()
        self.write({'state': 'failed'})

    def action_draft(self):
        """Re-open for corrections."""
        self.write({'state': 'draft'})

    def action_print_swab(self):
        """One-click PDF — allowed ONLY after Pass or Fail."""
        self.ensure_one()
        if self.state == 'draft':
            raise UserError(_(
                "The Swab Test must be Passed or Failed "
                "before the report can be printed."))
        return self.env.ref('fk_swab_testing.action_report_swab').report_action(self)


class SwabTestingLine(models.Model):
    _name = 'swab.testing.line'
    _description = 'Swab Testing Sample Line'
    _order = 'id'

    report_id = fields.Many2one(
        'swab.testing.report', string='Report',
        required=True, ondelete='cascade')

    sno = fields.Integer(string='S.No', compute='_compute_sno')

    sampling_receiving_date = fields.Date(
        string='Sampling / Receiving Date', default=fields.Date.context_today)
    testing_date = fields.Date(
        string='Testing Date', default=fields.Date.context_today)

    # AUTO-FETCHED + LOCKED: derived from the selected batch's source document
    customer_location = fields.Char(
        string='Customer / Location',
        compute='_compute_customer_location',
        store=True,
        readonly=True,
        help="Auto-fetched from the batch: vendor of the incoming receipt, "
             "or the customer of the SO that produced the batch. Not editable.")

    @api.depends('batch_id')
    def _compute_customer_location(self):
        for rec in self:
            rec.customer_location = rec._get_batch_partner_name()

    def _get_batch_partner_name(self):
        """Trace the lot back to its source partner."""
        self.ensure_one()
        if not self.batch_id:
            return False

        # 1) VENDOR: the incoming receipt (GRN) that brought this lot in
        in_line = self.env['stock.move.line'].search([
            ('lot_id', '=', self.batch_id.id),
            ('picking_id.picking_type_id.code', '=', 'incoming'),
        ], order='id', limit=1)
        if in_line.picking_id.partner_id:
            return in_line.picking_id.partner_id.name

        # 2) CUSTOMER: the MO that produced this lot -> origin SO -> customer
        mo = self.env['mrp.production'].search([
            ('lot_producing_ids', 'in', self.batch_id.id),
        ], limit=1)
        if mo and mo.origin:
            so = self.env['sale.order'].search(
                [('name', '=', mo.origin)], limit=1)
            if so.partner_id:
                return so.partner_id.name

        return False

    product_id = fields.Many2one(
        'product.product', string='Product Name',
        domain="[('product_type_custom', '=', 'finished')]")

    batch_id = fields.Many2one(
        'stock.lot', string='Batch',
        domain="[('product_id', '=', product_id)]")

    total_bacterial_count = fields.Char(string='Total Bacterial Count (cfu/gm)')
    s_aureus = fields.Char(string='S.aureus (cfu/gm)')
    total_coliforms = fields.Char(string='Total Coliforms (cfu/gm)')
    mold_yeast_count = fields.Char(string='Mold / Yeast Count (cfu/gm)')
    e_coli = fields.Char(string='E.coli (cfu/gm)')
    salmonella = fields.Char(string='Salmonella (/25g)')

    performed_by = fields.Many2one(
        'res.users', string='Performed By',
        default=lambda self: self.env.user, readonly=True)
    reporting_date = fields.Date(string='Reporting Date')
    remarks_status = fields.Char(string='Remarks / Status')

    @api.onchange('product_id')
    def _onchange_product_id_clear_batch(self):
        for rec in self:
            if rec.batch_id and rec.batch_id.product_id != rec.product_id:
                rec.batch_id = False

    @api.depends('report_id.line_ids')
    def _compute_sno(self):
        for report in self.mapped('report_id'):
            for index, line in enumerate(report.line_ids, start=1):
                line.sno = index
        for rec in self.filtered(lambda l: not l.report_id):
            rec.sno = 0
