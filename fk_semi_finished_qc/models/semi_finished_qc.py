from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import re


class SemiFinishedQC(models.Model):
    _name = 'semi.finished.qc'
    _description = 'Semi-Finished Quality Check (Post-Mixing)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # ==========================================
    # AUTO-FILLED FIELDS (From Sale Order)
    # ==========================================
    name = fields.Char(
        string='Document No', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True)

    date = fields.Date(
        string='Issue Date', default=fields.Date.context_today,
        readonly=True, tracking=True)

    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', required=True, tracking=True,
        help="Select the Sale Order for which the mixing is completed.")

    product_id = fields.Many2one(
        'product.product', string='Product Name', tracking=True,
        domain="[('id', 'in', allowed_recipe_product_ids)]")

    product_category = fields.Selection([
        ('recipe_mixes', 'Recipe Mixes'),
        ('plain_spices', 'Plain Spices'),
        ('powdered_desserts', 'Powdered Desserts'),
    ], string='Product Category', tracking=True,
        help="Classification of the tested product (per the FG Test Report header).")

    allowed_recipe_product_ids = fields.Many2many(
        'product.product',
        string='Allowed Products',
        compute='_compute_allowed_recipe_product_ids'
    )

    color_id = fields.Many2one(
        'color.parameter', string='Observed Color',
        tracking=True,
        help="Type to select a predefined color or manually enter a new one based on actual appearance.")

    allowed_color_ids = fields.Many2many(
        'color.parameter',
        string='Allowed Colors',
        compute='_compute_allowed_color_ids'
    )

    batch_no = fields.Many2one(
        'stock.lot', string='Batch / Lot No',
        tracking=True,
        help="Auto-fetched from Manufacturing Order based on Sale Order and Product.")

    # ==========================================
    # MANUAL FIELDS (Entered by QC Team)
    # ==========================================
    performed_by_id = fields.Many2one(
        'res.users', string='Performed By',
        default=lambda self: self.env.user, tracking=True,
        help="The person who performed this Semi-Finished Quality Check.")

    sample_received_date = fields.Date(
        string='Sample Received Date', tracking=True)

    test_date = fields.Date(
        string='Test Date', tracking=True)

    temperature = fields.Float(
        string='Environment Temp (°C)', tracking=True)

    # ==========================================
    # STATUS & LINES
    # ==========================================
    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', tracking=True)

    line_ids = fields.One2many(
        'semi.finished.qc.line', 'qc_id', string='Test Parameters')

    # ==========================================
    # QUANTITY CONTROL (system-driven, NO partials)
    # ==========================================
    mixed_qty = fields.Float(
        string='Mixed Qty', compute='_compute_qty_control',
        help="Quantity mixed and marked as Done in Mixing Slips.")
    qc_passed_qty = fields.Float(string='QC Passed Qty', compute='_compute_qty_control')
    qc_failed_qty = fields.Float(string='QC Failed Qty', compute='_compute_qty_control')
    qty_available = fields.Float(string='Available for QC', compute='_compute_qty_control')

    qty_to_qc = fields.Float(
        string='Qty to QC', readonly=True,
        help="System-driven: locked to the pending mixed quantity. "
             "Partial QC of a mixed batch is not allowed.")

    # ==========================================
    # SO GATING: quantity-aware SO / product dropdowns
    # ==========================================
    allowed_sale_order_ids = fields.Many2many(
        'sale.order', string='Allowed Sale Orders',
        compute='_compute_allowed_sale_order_ids')

    def _compute_allowed_sale_order_ids(self):
        """SO becomes selectable only after its Line Clearance sheet
        is confirmed AND it has mixed-but-not-QC'd quantity available."""
        for rec in self:
            confirmed_clearances = self.env['line.clearance'].search([
                ('state', '=', 'confirmed'),
            ])
            candidate_sos = confirmed_clearances.mapped('sale_order_id')

            allowed = self.env['sale.order']
            for so in candidate_sos:
                if rec._eligible_products(so):
                    allowed |= so
            rec.allowed_sale_order_ids = allowed

    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        """Only SO products that:
        1. have a confirmed Line Clearance,
        2. have a DONE Mixing Slip,
        3. still have pending mixed quantity (mixed − passed − failed > 0),
        4. and are not held by an open (draft/in_progress) SFG QC."""
        for rec in self:
            if not rec.sale_order_id:
                rec.allowed_recipe_product_ids = False
                continue
            rec.allowed_recipe_product_ids = rec._eligible_products(rec.sale_order_id)

    @api.depends('product_id')
    def _compute_allowed_color_ids(self):
        """Fetch colors defined on the selected product's Colors tab"""
        for rec in self:
            if rec.product_id:
                rec.allowed_color_ids = rec.product_id.product_tmpl_id.color_parameter_ids
            else:
                rec.allowed_color_ids = False

    # ==========================================
    # ONCHANGES
    # ==========================================
    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        """Clear downstream fields when the Sale Order changes."""
        if self.sale_order_id:
            self.product_id = False
            self.batch_no = False
            self.line_ids = [(5, 0, 0)]
            self.qty_to_qc = 0.0

    @api.onchange('product_id')
    def _onchange_product_id_fetch_batch(self):
        """Fetch Batch/Lot from the MO, auto-load parameters, and lock the QC quantity."""
        batch = False
        if self.sale_order_id and self.product_id:
            so_name = self.sale_order_id.name
            mo = self.env['mrp.production'].search([
                ('origin', 'ilike', f'%{so_name}%'),
                ('product_id', '=', self.product_id.id)
            ], limit=1, order='id desc')

            if mo and mo.lot_producing_ids:
                batch = mo.lot_producing_ids[0].id

        if not batch and self.product_id:
            lot = self.env['stock.lot'].search([
                ('product_id', '=', self.product_id.id),
                ('company_id', '=', self.env.company.id)
            ], order='id desc', limit=1)
            if lot:
                batch = lot.id

        self.batch_no = batch

        self.line_ids = [(5, 0, 0)]
        if self.product_id:
            self._load_default_parameters()
            if not self.line_ids:
                return {'warning': {
                    'title': _('No Test Parameters'),
                    'message': _(
                        'Product %s has no Semi-Finished Quality Parameters configured. '
                        'Please go to the Product page and add them under the "Semi Finished Goods QC Parameters" tab.'
                    ) % self.product_id.display_name,
                }}

        # NEW: show the locked quantity as soon as the product is selected
        self.qty_to_qc = self.qty_available

    # ==========================================
    # ACTIONS & VALIDATION
    # ==========================================
    def action_start(self):
        self.ensure_one()

        # NEW: LOCK the quantity now; block if nothing is available yet
        self._refresh_qty_to_qc()
        if not self.qty_to_qc:
            raise UserError(_(
                "No mixed quantity is available for Semi-Finished QC yet. "
                "Complete the Mixing Slip for this product first."))

        # SELF-HEAL: remove ghost lines saved without a parameter
        ghost_lines = self.line_ids.filtered(lambda l: not l.parameter_id)
        if ghost_lines:
            ghost_lines.unlink()

        if not self.line_ids:
            self._load_default_parameters()
        self.write({'state': 'in_progress'})

    def _validate_qc_lines(self, lines, result):
        if not lines:
            return

        def _label(line):
            return line.parameter_id.name or _('Unnamed Parameter')

        incomplete_lines = lines.filtered(
            lambda l: l.status == 'pending'
                      or (not l.result and l.status != 'na')
        )
        if incomplete_lines:
            raise UserError(_(
                "QC Validation Error:\n"
                "The following test parameters are missing either a result "
                "or a status:\n\n• %s"
            ) % '\n• '.join([_label(l) for l in incomplete_lines]))

        if result == 'Pass':
            failed_no_remarks = lines.filtered(
                lambda l: l.status == 'fail' and not l.remarks
            )
            if failed_no_remarks:
                raise UserError(_(
                    "QC Validation Error:\n"
                    "The following parameters are marked as FAILED but have "
                    "no remarks:\n\n• %s"
                ) % '\n• '.join([_label(l) for l in failed_no_remarks]))

            forced_pass_no_remarks = lines.filtered(
                lambda l: l.status == 'pass'
                          and l._expected_numeric_status() == 'fail'
                          and not l.remarks
            )
            if forced_pass_no_remarks:
                raise UserError(_(
                    "QC Validation Error:\n"
                    "The following parameters EXCEED their specification "
                    "limit but are manually marked as PASS. Please add "
                    "remarks justifying this manual override:\n\n• %s"
                ) % '\n• '.join([_label(l) for l in forced_pass_no_remarks]))

    def action_pass(self):
        self.ensure_one()

        # NEW: a QC with no locked quantity can never be passed
        if not self.qty_to_qc:
            raise UserError(_(
                "This Semi-Finished QC has no mixed quantity to evaluate. "
                "It cannot be passed."))

        if not self.color_id:
            raise UserError(_(
                "QC Validation Error:\n"
                "Observed Color is mandatory before passing the QC."
            ))

        if not self.line_ids:
            raise UserError(_("No test parameters found."))

        self._validate_qc_lines(self.line_ids, 'Pass')
        self.write({'state': 'passed'})

        if self.sale_order_id and self.product_id:
            mo = self.env['mrp.production'].search([
                ('origin', '=', self.sale_order_id.name),
                ('product_id', '=', self.product_id.id)
            ], limit=1)

            if mo:
                if not mo.lot_producing_ids:
                    mo.action_generate_serial()
                if mo.lot_producing_ids and not self.batch_no:
                    self.batch_no = mo.lot_producing_ids[0]

    def action_fail(self):
        self.ensure_one()

        # NEW: a QC with no locked quantity can never be failed
        if not self.qty_to_qc:
            raise UserError(_(
                "This Semi-Finished QC has no mixed quantity to evaluate. "
                "It cannot be failed."))

        if not self.line_ids:
            raise UserError(_("No test parameters found."))

        self._validate_qc_lines(self.line_ids, 'Fail')

        failed_lines = self.line_ids.filtered(lambda l: l.status == 'fail')
        if not failed_lines:
            raise UserError(_(
                "QC Validation Error:\n"
                "This Quality Check cannot be failed because ALL test "
                "parameters have Passed (or are N/A)."
            ))

        self.write({'state': 'failed'})

    def _load_default_parameters(self):
        self.ensure_one()
        if not self.product_id:
            return

        lines = []
        for param_line in self.product_id.product_tmpl_id.semi_fg_specs:
            lines.append((0, 0, {
                'parameter_id': param_line.parameter_id.id,
                'specification': param_line.specification or param_line.parameter_id.default_specification,
                'condition': param_line.condition or param_line.parameter_id.default_condition,
            }))

        if lines:
            self.line_ids = lines

    def action_reload_default_parameters(self):
        """Wipe current test lines and reload them from the product's
        'Semi Finished Goods QC Parameters' tab."""
        for rec in self:
            if rec.state not in ('draft', 'in_progress'):
                raise UserError(_(
                    "Parameters can only be reloaded while the QC is "
                    "Draft or In Progress."))
            if not rec.product_id:
                raise UserError(_("Select a Product before reloading parameters."))

            # Clear existing lines completely
            rec.line_ids.unlink()

            # Reload from product master
            rec._load_default_parameters()
            rec.message_post(body=_("<b>Test parameters reloaded</b> from the product master."))

    # ==========================================
    # QUANTITY CONTROL HELPERS
    # ==========================================
    def _get_mixed_qty(self, so, product):
        """Sum of qty_producing from MRS linked to DONE mixing slips."""
        slips = self.env['mixing.slip'].search([
            ('sale_order_id', '=', so.id),
            ('recipe_product_id', '=', product.id),
            ('state', '=', 'done'),
        ])
        return sum(slips.mapped('mrs_id.qty_producing'))

    def _get_consumed_qtys(self, so, product, exclude_id=False):
        domain = [('sale_order_id', '=', so.id), ('product_id', '=', product.id)]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        qcs = self.search(domain)
        return (sum(qcs.filtered(lambda q: q.state == 'passed').mapped('qty_to_qc')),
                sum(qcs.filtered(lambda q: q.state == 'failed').mapped('qty_to_qc')))

    @api.depends('sale_order_id', 'product_id', 'state')
    def _compute_qty_control(self):
        for rec in self:
            if rec.sale_order_id and rec.product_id:
                passed, failed = rec._get_consumed_qtys(
                    rec.sale_order_id, rec.product_id, rec.id)
                rec.mixed_qty = rec._get_mixed_qty(
                    rec.sale_order_id, rec.product_id)
                rec.qc_passed_qty = passed
                rec.qc_failed_qty = failed
                rec.qty_available = max(0.0, rec.mixed_qty - passed - failed)
            else:
                rec.mixed_qty = rec.qc_passed_qty = 0.0
                rec.qc_failed_qty = rec.qty_available = 0.0

    def _refresh_qty_to_qc(self):
        """Lock qty_to_qc to the currently pending mixed quantity."""
        for rec in self:
            if rec.state in ('draft', 'in_progress'):
                rec.qty_to_qc = rec.qty_available

    def _eligible_products(self, so):
        """Products with done mixing + confirmed LC + available qty > 0 + no open QC."""
        confirmed_lcs = self.env['line.clearance'].search([
            ('sale_order_id', '=', so.id), ('state', '=', 'confirmed'),
        ])
        lc_products = confirmed_lcs.mapped('product_id')

        done_slips = self.env['mixing.slip'].search([
            ('sale_order_id', '=', so.id), ('state', '=', 'done'),
        ])
        mixed_products = done_slips.mapped('recipe_product_id')

        eligible = self.env['product.product']
        candidates = so.order_line.mapped('product_id') & lc_products & mixed_products

        for product in candidates:
            mixed = self._get_mixed_qty(so, product)
            passed_q, failed_q = self._get_consumed_qtys(so, product)
            if mixed - passed_q - failed_q <= 0:
                continue
            if self.search_count([
                ('sale_order_id', '=', so.id), ('product_id', '=', product.id),
                ('state', 'in', ('draft', 'in_progress')),
            ]):
                continue  # an open QC already holds this product
            eligible |= product
        return eligible

    # ==========================================
    # HARD BACKSTOP: no zero / no over-consumption on completed QCs
    # ==========================================
    @api.constrains('qty_to_qc', 'state')
    def _check_qty_not_partial(self):
        for rec in self:
            if not (rec.sale_order_id and rec.product_id):
                continue
            if rec.state in ('passed', 'failed'):
                mixed = rec._get_mixed_qty(rec.sale_order_id, rec.product_id)
                passed_q, failed_q = rec._get_consumed_qtys(
                    rec.sale_order_id, rec.product_id, rec.id)
                max_allowed = mixed - passed_q - failed_q
                if rec.qty_to_qc <= 0 or rec.qty_to_qc > max_allowed:
                    raise ValidationError(_(
                        "Semi-Finished QC quantity (%s) must match the pending mixed "
                        "quantity (max %s) for %s. Partial QC of a mixed "
                        "batch is not allowed.") % (
                                              rec.qty_to_qc, max_allowed, rec.product_id.display_name))

    # ==========================================
    # SEQUENCE & CRUD
    # ==========================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('semi.finished.qc') or _('New')
        records = super().create(vals_list)
        # NEW: pre-fill the locked quantity for records created with a product
        records.filtered(
            lambda r: r.product_id and r.state == 'draft' and not r.qty_to_qc
        )._refresh_qty_to_qc()
        return records


class SemiFinishedQCLine(models.Model):
    _name = 'semi.finished.qc.line'
    _description = 'Semi-Finished QC Test Line'
    _order = 'id'

    qc_id = fields.Many2one('semi.finished.qc', string='QC Document', ondelete='cascade', required=True)

    parameter_id = fields.Many2one('kalsal.quality.parameter', string='Test Parameter')
    specification = fields.Char(string='Specification / Limit')

    condition = fields.Selection([
        ('nmt', 'Not More Than'),
        ('nlt', 'Not Less Than'),
    ], string='Condition')

    test_method = fields.Char(string='Test Method')
    specification_ref = fields.Char(string='Spec. Reference')
    result = fields.Char(string='Result')

    status = fields.Selection([
        ('pass', 'Pass'),
        ('fail', 'Fail'),
        ('na', 'N/A'),
        ('pending', 'Pending'),
    ], string='Status', default='pending', compute='_compute_status', store=True, readonly=False)

    remarks = fields.Char(string='Remarks')

    @api.onchange('parameter_id')
    def _onchange_parameter_id_defaults(self):
        for rec in self:
            if rec.parameter_id:
                if not rec.specification:
                    rec.specification = rec.parameter_id.default_specification
                if not rec.condition:
                    rec.condition = rec.parameter_id.default_condition

    @api.depends('result', 'specification', 'condition')
    def _compute_status(self):
        for rec in self:
            if not rec.result:
                if rec.status not in ['na']:
                    rec.status = 'pending'
                continue

            target_val = rec._parse_to_float(rec.specification)
            actual_val = rec._parse_to_float(rec.result)

            if target_val is None or actual_val is None:
                continue

            if rec.condition == 'nmt':
                rec.status = 'pass' if actual_val <= target_val else 'fail'
            elif rec.condition == 'nlt':
                rec.status = 'pass' if actual_val >= target_val else 'fail'
            else:
                continue

    def _expected_numeric_status(self):
        self.ensure_one()
        target_val = self._parse_to_float(self.specification)
        actual_val = self._parse_to_float(self.result)
        if target_val is None or actual_val is None:
            return False
        if self.condition == 'nlt':
            return 'pass' if actual_val >= target_val else 'fail'
        if self.condition == 'nmt':
            return 'pass' if actual_val <= target_val else 'fail'
        return False

    def _parse_to_float(self, value_str):
        if not value_str:
            return None
        clean_str = str(value_str).strip()
        match = re.search(r'[-+]?(?:\d*\.\d+|\d+)', clean_str)
        if match:
            try:
                return float(match.group())
            except ValueError:
                return None
        return None
