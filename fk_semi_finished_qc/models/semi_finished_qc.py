from odoo import models, fields, api, _
from odoo.exceptions import UserError
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
    # SO GATING: only SOs whose Line Clearance is CONFIRMED
    # ==========================================
    allowed_sale_order_ids = fields.Many2many(
        'sale.order', string='Allowed Sale Orders',
        compute='_compute_allowed_sale_order_ids')

    def _compute_allowed_sale_order_ids(self):
        """SO becomes selectable only after its Line Clearance sheet
        is confirmed (which itself requires a Mixing Document)."""
        for rec in self:
            confirmed_clearances = self.env['line.clearance'].search([
                ('state', '=', 'confirmed'),
            ])
            rec.allowed_sale_order_ids = confirmed_clearances.mapped('sale_order_id')

    # ==========================================
    # COMPUTES
    # ==========================================
    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        """Fetch products from the selected Sale Order lines, but ONLY those
        whose mixing process is marked as DONE."""
        for rec in self:
            if not rec.sale_order_id:
                rec.allowed_recipe_product_ids = False
                continue

            # 1. Get all products on this Sale Order
            so_products = rec.sale_order_id.order_line.mapped('product_id')

            # 2. Find products that have a completed Mixing Slip for this SO
            mixed_products = self.env['mixing.slip'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', '=', 'done')
            ]).mapped('recipe_product_id')  # <-- mapped to recipe_product_id

            # 3. Intersect: only allow SO products that have finished mixing
            rec.allowed_recipe_product_ids = so_products.filtered(
                lambda p: p in mixed_products
            )

    @api.depends('product_id')
    def _compute_allowed_color_ids(self):
        """Fetch colors defined on the selected product's Colors tab"""
        for rec in self:
            if rec.product_id:
                rec.allowed_color_ids = rec.product_id.product_tmpl_id.color_parameter_ids
            else:
                rec.allowed_color_ids = False

    # ==========================================
    # ONCHANGES — THESE MUST LIVE ON semi.finished.qc (HEADER FIELDS)
    # ==========================================
    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        """Clear downstream fields when the Sale Order changes."""
        if self.sale_order_id:
            self.product_id = False
            self.batch_no = False
            self.line_ids = [(5, 0, 0)]

    @api.onchange('product_id')
    def _onchange_product_id_fetch_batch(self):
        """Fetch Batch/Lot from the MO (flexible matching + Lot fallback)
        AND auto-load the test parameters, exactly like the 1st QC."""
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

        # NEW: auto-fill test parameters the moment the product is selected.
        # They render LOCKED while state == 'draft' because the list view
        # is readonly="state != 'in_progress'".
        # NEW: auto-fill test parameters the moment the product is selected.
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

    # ==========================================
    # ACTIONS & VALIDATION
    # ==========================================
    def action_start(self):
        self.ensure_one()
        # SELF-HEAL: remove ghost lines saved without a parameter
        # (records created before the force_save fix)
        ghost_lines = self.line_ids.filtered(lambda l: not l.parameter_id)
        if ghost_lines:
            ghost_lines.unlink()

        if not self.line_ids:
            self._load_default_parameters()
        self.write({'state': 'in_progress'})

    def _validate_qc_lines(self, lines, result):
        """
        Same gatekeeping as your Raw Material 1st QC:
        - Every parameter line must have a result (unless status == 'na')
        - No line may remain 'pending'
        - When Passing, any line marked 'fail' must have remarks
        - When Passing, any numeric line forced to 'pass' must have remarks
        """
        if not lines:
            return

        def _label(line):
            return line.parameter_id.name or _('Unnamed Parameter')

        # 1. Block if any line is still pending or missing a result
        incomplete_lines = lines.filtered(
            lambda l: l.status == 'pending'
                      or (not l.result and l.status != 'na')
        )
        if incomplete_lines:
            raise UserError(_(
                "QC Validation Error:\n"
                "The following test parameters are missing either a result "
                "or a status. Please fill in the test results, or manually "
                "change the status (for string-based results you must set "
                "Pass / Fail / N/A manually):\n\n• %s"
            ) % '\n• '.join([_label(l) for l in incomplete_lines]))

        # 2. When Passing, failed parameters must carry remarks
        if result == 'Pass':
            failed_no_remarks = lines.filtered(
                lambda l: l.status == 'fail' and not l.remarks
            )
            if failed_no_remarks:
                raise UserError(_(
                    "QC Validation Error:\n"
                    "The following parameters are marked as FAILED but have "
                    "no remarks. Please add remarks explaining the failure "
                    "before proceeding:\n\n• %s"
                ) % '\n• '.join([_label(l) for l in failed_no_remarks]))

            # Forced-pass guard: numeric line violating its limit but manually
            # marked PASS must carry remarks (audit trail)
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
                    "remarks justifying this manual override before "
                    "proceeding:\n\n• %s"
                ) % '\n• '.join([_label(l) for l in forced_pass_no_remarks]))

    def action_pass(self):
        self.ensure_one()

        # NEW: Color is a test — cannot pass without an observed color
        if not self.color_id:
            raise UserError(_(
                "QC Validation Error:\n"
                "Observed Color is mandatory before passing the QC. "
                "Please select the observed color for this batch "
                "(Color is a test parameter)."
            ))

        if not self.line_ids:
            raise UserError(_(
                "No test parameters found. "
                "Please load test parameters before passing the QC."
            ))
        self._validate_qc_lines(self.line_ids, 'Pass')
        self.write({'state': 'passed'})

        # ==========================================
        # POST-PASS: LOT HANDLING (rework-safe)
        # ==========================================
        if self.sale_order_id and self.product_id:
            mo = self.env['mrp.production'].search([
                ('origin', '=', self.sale_order_id.name),
                ('product_id', '=', self.product_id.id)
            ], limit=1)

            if mo:
                # FIRST PASS: MO has no lot yet -> generate it (original behavior)
                if not mo.lot_producing_ids:
                    mo.action_generate_serial()

                # REWORK PASS: lot already exists -> reuse it, never regenerate
                if mo.lot_producing_ids and not self.batch_no:
                    self.batch_no = mo.lot_producing_ids[0]

    def action_fail(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_(
                "No test parameters found. "
                "Please load test parameters before failing the QC."))

        # SAME GATEKEEPING AS THE 1st QC: every parameter must have a
        # result/status before the QC can be failed.
        self._validate_qc_lines(self.line_ids, 'Fail')

        # NEW GUARD: a QC cannot be failed if no test actually failed
        failed_lines = self.line_ids.filtered(lambda l: l.status == 'fail')
        if not failed_lines:
            raise UserError(_(
                "QC Validation Error:\n"
                "This Quality Check cannot be failed because ALL test "
                "parameters have Passed (or are N/A). A QC can only be "
                "marked as Failed when at least one parameter has failed."
            ))

        self.write({'state': 'failed'})

    def _load_default_parameters(self):
        """Fetches parameters specifically from the Semi-Finished Goods QC Parameters tab."""
        self.ensure_one()
        if not self.product_id or self.line_ids:
            return

        lines = []
        # CHANGED: Fetch from 'semi_fg_specs' instead of the old generic 'quality_param_line_ids'
        for param_line in self.product_id.product_tmpl_id.semi_fg_specs:
            lines.append((0, 0, {
                'parameter_id': param_line.parameter_id.id,
                'specification': param_line.specification or param_line.parameter_id.default_specification,
                'condition': param_line.condition or param_line.parameter_id.default_condition,
            }))

        if lines:
            self.line_ids = lines

    # ==========================================
    # SEQUENCE & CRUD
    # ==========================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('semi.finished.qc') or _('New'
                                                                                             )
        return super().create(vals_list)


class SemiFinishedQCLine(models.Model):
    _name = 'semi.finished.qc.line'
    _description = 'Semi-Finished QC Test Line'
    _order = 'id'

    qc_id = fields.Many2one('semi.finished.qc', string='QC Document', ondelete='cascade', required=True)

    # AUTO-FILLED (From Product Master)
    parameter_id = fields.Many2one('kalsal.quality.parameter', string='Test Parameter')
    specification = fields.Char(string='Specification / Limit')

    # Same Condition column as the 1st QC
    condition = fields.Selection([
        ('nmt', 'Not More Than'),
        ('nlt', 'Not Less Than'),
    ], string='Condition')

    # MANUAL ENTRY
    test_method = fields.Char(string='Test Method', help="e.g., Titration, GC, Visual")
    specification_ref = fields.Char(string='Spec. Reference', help="e.g., ISO-XXX, Internal SOP-01")
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
        """Mirror the 1st QC behaviour: auto-fill defaults when a parameter is picked manually."""
        for rec in self:
            if rec.parameter_id:
                if not rec.specification:
                    rec.specification = rec.parameter_id.default_specification
                if not rec.condition:
                    rec.condition = rec.parameter_id.default_condition

    # ONLY ONE _compute_status (1st-QC style). The old duplicate is REMOVED.
    @api.depends('result', 'specification', 'condition')
    def _compute_status(self):
        """Identical behaviour to the Raw Material 1st QC:
        - Numeric results are strictly evaluated against the Condition (NMT/NLT).
        - Text results NEVER overwrite the user's manual dropdown selection.
        """
        for rec in self:
            # No result yet -> pending (but never wipe a manual N/A)
            if not rec.result:
                if rec.status not in ['na']:
                    rec.status = 'pending'
                continue

            target_val = rec._parse_to_float(rec.specification)
            actual_val = rec._parse_to_float(rec.result)

            # Text-based parameter (e.g. Taste/Color): keep manual selection
            if target_val is None or actual_val is None:
                continue

            # Strict numeric evaluation (cannot be bypassed manually)
            if rec.condition == 'nmt':
                rec.status = 'pass' if actual_val <= target_val else 'fail'
            elif rec.condition == 'nlt':
                rec.status = 'pass' if actual_val >= target_val else 'fail'
            else:
                continue

    def _expected_numeric_status(self):
        """Pure numeric evaluation (what the status SHOULD be).
        Returns False for text-based parameters (Taste, Color, etc.)."""
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
        """Extracts the first number from a string (e.g., 'Not More Than 10' -> 10)"""
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
