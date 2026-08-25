from odoo import models, fields, api, _
from odoo.exceptions import UserError
import re


class FinishedQC(models.Model):
    _name = 'finished.qc'
    _description = 'Finished Goods Quality Check (FG Test Report)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # ==========================================
    # ANCHOR / DOCUMENT INFORMATION
    # ==========================================
    name = fields.Char(
        string='Document No', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True)

    date = fields.Date(
        string='Issue Date', default=fields.Date.context_today,
        readonly=True, tracking=True)

    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', required=True, tracking=True,
        domain="[('id', 'in', allowed_sale_order_ids)]",
        help="Only Sale Orders whose Semi-Finished QC has passed are selectable.")

    product_id = fields.Many2one(
        'product.product', string='Product Name', tracking=True,
        domain="[('id', 'in', allowed_recipe_product_ids)]")

    allowed_recipe_product_ids = fields.Many2many(
        'product.product', string='Allowed Products',
        compute='_compute_allowed_recipe_product_ids')

    batch_no = fields.Many2one(
        'stock.lot', string='Batch / Lot No', tracking=True,
        help="Auto-fetched from Manufacturing Order based on Sale Order and Product.")

    customer_id = fields.Many2one(
        related='sale_order_id.partner_id', string='Customer / Location', store=True)

    performed_by_id = fields.Many2one(
        'res.users', string='Performed By',
        default=lambda self: self.env.user, tracking=True)

    # ==========================================
    # SAMPLING & ENVIRONMENT
    # ==========================================
    sample_condition = fields.Selection([
        ('satisfactory', 'Satisfactory'),
        ('unsatisfactory', 'Unsatisfactory'),
    ], string='Sample Condition', default='satisfactory', tracking=True)

    temperature = fields.Float(string='Environment Temp (°C)', tracking=True)

    sampling_date = fields.Date(string='Sampling / Receiving Date', tracking=True)
    testing_date = fields.Date(string='Testing Date', tracking=True)
    reporting_date = fields.Date(string='Reporting Date', tracking=True)

    # ==========================================
    # STATUS & LINES (Two separate inverse fields = NO domains needed)
    # ==========================================
    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', tracking=True)

    standard_line_ids = fields.One2many(
        'finished.qc.line', 'standard_qc_id', string='Standard Test Parameters')

    microbiological_line_ids = fields.One2many(
        'finished.qc.line', 'micro_qc_id', string='Microbiological Test Parameters')

    # ==========================================
    # PATHOGEN NAME MATCHER (case/punctuation tolerant)
    # ==========================================
    MICRO_PARAM_KEYS = ['e.coli', 'e-coli', 'ecoli', 'salmonella']

    def _is_micro_param(self, name):
        """Matches: E.coli, E-Coli, E COLI, ecoli, Salmonella, Salmonella Test, etc."""
        clean = (name or '').strip().lower().replace(' ', '')
        return any(key in clean for key in self.MICRO_PARAM_KEYS)

    # ==========================================
    # GATING: only SOs whose Semi-Finished QC PASSED
    # ==========================================
    allowed_sale_order_ids = fields.Many2many(
        'sale.order', string='Allowed Sale Orders',
        compute='_compute_allowed_sale_order_ids')

    def _compute_allowed_sale_order_ids(self):
        """SO is selectable only if at least one of its products still needs
        the Final QC (passed SFG + confirmed FG Report + not Final-QC'd)."""
        for rec in self:
            # SO -> products with passed Semi-Finished QC
            so_passed = {}
            for qc in self.env['semi.finished.qc'].search([('state', '=', 'passed')]):
                if qc.sale_order_id:
                    so_passed.setdefault(qc.sale_order_id.id, set()).add(qc.product_id.id)

            # SO -> products included in a confirmed FG Report
            so_reported = {}
            for rpt in self.env['fg.reporting'].search([('state', '=', 'confirmed')]):
                if rpt.sale_order_id:
                    so_reported.setdefault(
                        rpt.sale_order_id.id, set()
                    ).update(rpt.line_ids.mapped('product_id').ids)

            # SO -> products that already have a completed Final QC
            so_done = {}
            for fqc in self.search([('state', 'in', ('passed', 'failed'))]):
                if fqc.sale_order_id:
                    so_done.setdefault(fqc.sale_order_id.id, set()).add(fqc.product_id.id)

            allowed = self.env['sale.order']
            for so_id, passed_ids in so_passed.items():
                remaining = (passed_ids & so_reported.get(so_id, set())) - so_done.get(so_id, set())
                if remaining:
                    allowed |= self.env['sale.order'].browse(so_id)
            rec.allowed_sale_order_ids = allowed

    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        """Only SO products that:
        1. passed Semi-Finished QC,
        2. are included in a CONFIRMED FG Report,
        3. and have NOT been Final-QC'd yet (no passed/failed Finished QC)."""
        for rec in self:
            if not rec.sale_order_id:
                rec.allowed_recipe_product_ids = False
                continue

            so_products = rec.sale_order_id.order_line.mapped('product_id')

            passed_products = self.env['semi.finished.qc'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', '=', 'passed'),
            ]).mapped('product_id')

            reported_products = self.env['fg.reporting'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', '=', 'confirmed'),
            ]).mapped('line_ids.product_id')

            # NEW: products that already have a completed Final QC (passed or failed)
            done_products = self.search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', 'in', ('passed', 'failed')),
            ]).mapped('product_id')

            rec.allowed_recipe_product_ids = so_products.filtered(
                lambda p: p in passed_products
                          and p in reported_products
                          and p not in done_products
            )

    @api.constrains('sale_order_id', 'product_id')
    def _check_fg_reporting_confirmed(self):
        for rec in self:
            if not (rec.sale_order_id and rec.product_id):
                continue
            confirmed = self.env['fg.reporting'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', '=', 'confirmed'),
                ('line_ids.product_id', '=', rec.product_id.id),
            ], limit=1)
            if not confirmed:
                raise UserError(_(
                    "Finished Goods QC Blocked:\n"
                    "The FG Reporting for %s / %s has not been CONFIRMED yet. "
                    "Complete the FG Reporting before performing the Final QC."
                ) % (rec.sale_order_id.name, rec.product_id.display_name))

    # ==========================================
    # ONCHANGES
    # ==========================================
    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        if self.sale_order_id:
            self.product_id = False
            self.batch_no = False
            self.standard_line_ids = [(5, 0, 0)]
            self.microbiological_line_ids = [(5, 0, 0)]

    @api.onchange('product_id')
    def _onchange_product_id_fetch_batch(self):
        """Fetch Batch/Lot from the MO and auto-load the fg_specs parameters."""
        batch = False
        if self.sale_order_id and self.product_id:
            so_name = self.sale_order_id.name
            mo = self.env['mrp.production'].search([
                ('origin', 'like', f'{so_name}%'),
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

        self.standard_line_ids = [(5, 0, 0)]
        self.microbiological_line_ids = [(5, 0, 0)]

        if self.product_id:
            self._load_default_parameters()
            if not self.standard_line_ids and not self.microbiological_line_ids:
                return {'warning': {
                    'title': _('No Test Parameters'),
                    'message': _(
                        'Product %s has no Finished Goods Quality Parameters configured. '
                        'Please go to the Product page and add them under the '
                        '"Finished Goods QC Parameters" tab.'
                    ) % self.product_id.display_name,
                }}

    # ==========================================
    # ACTIONS & VALIDATION
    # ==========================================
    def action_start(self):
        self.ensure_one()

        # SELF-HEAL: move any misclassified lines to the correct tab
        # (fixes records saved before the tolerant matcher existed)
        wrong_standard = self.standard_line_ids.filtered(
            lambda l: self._is_micro_param(l.test_parameter))
        wrong_micro = self.microbiological_line_ids.filtered(
            lambda l: not self._is_micro_param(l.test_parameter))
        if wrong_standard:
            wrong_standard.write({'standard_qc_id': False, 'micro_qc_id': self.id})
        if wrong_micro:
            wrong_micro.write({'micro_qc_id': False, 'standard_qc_id': self.id})

        # Clean ghost lines (rows saved without a parameter)
        ghost_standard = self.standard_line_ids.filtered(lambda l: not l.parameter_id)
        ghost_micro = self.microbiological_line_ids.filtered(lambda l: not l.parameter_id)
        if ghost_standard:
            ghost_standard.unlink()
        if ghost_micro:
            ghost_micro.unlink()

        if not self.standard_line_ids and not self.microbiological_line_ids:
            self._load_default_parameters()
        self.write({'state': 'in_progress'})

    def _collect_validation_errors(self, lines, result):
        """Collects EVERY validation problem into a list so the user gets
        ONE single error listing all issues, instead of sequential popups."""
        errors = []
        if not lines:
            return errors

        def _label(line):
            return line.parameter_id.name or _('Unnamed Parameter')

        # 1. Missing results / pending statuses
        #    (micro lines are "complete" once detection_result is chosen)
        incomplete_lines = lines.filtered(
            lambda l: l.status == 'pending'
                      or (not l.result and l.status != 'na' and not l.micro_qc_id)
        )
        if incomplete_lines:
            errors.append(_(
                "❌ The following parameters are missing a result or status:\n• %s"
            ) % '\n• '.join([_label(l) for l in incomplete_lines]))

        if result == 'Pass':
            # 2. Failed lines without remarks
            #    (exclude micro lines: pathogens are reported by the
            #     dedicated "PRESENT but no remarks" gate in action_pass)
            failed_no_remarks = lines.filtered(
                lambda l: l.status == 'fail' and not l.remarks and not l.micro_qc_id
            )
            if failed_no_remarks:
                errors.append(_(
                    "❌ The following parameters are FAILED but have no remarks:\n• %s"
                ) % '\n• '.join([_label(l) for l in failed_no_remarks]))

            # 3. Forced pass without remarks (unchanged)
            forced_pass_no_remarks = lines.filtered(
                lambda l: l.status == 'pass'
                          and l._expected_status() == 'fail'
                          and not l.remarks
            )
            if forced_pass_no_remarks:
                errors.append(_(
                    "❌ The following parameters violate their specification "
                    "but are manually marked PASS without remarks:\n• %s"
                ) % '\n• '.join([_label(l) for l in forced_pass_no_remarks]))

        return errors

    def action_pass(self):
        self.ensure_one()

        all_lines = self.standard_line_ids | self.microbiological_line_ids
        if not all_lines:
            raise UserError(_("No test parameters found. Please load parameters before passing."))

        errors = []

        # 1. GATE: Sample Condition must be Satisfactory
        if self.sample_condition != 'satisfactory':
            errors.append(_(
                "❌ The Sample Condition is marked as UNSATISFACTORY. "
                "A Finished Goods QC cannot be passed with an unsatisfactory sample."
            ))

        # 2. GATE: Every pathogen must have an explicit choice
        unselected = self.microbiological_line_ids.filtered(
            lambda l: not l.detection_result)
        if unselected:
            errors.append(_(
                "❌ Please select 'Absent' or 'Present' for:\n• %s"
            ) % '\n• '.join(unselected.mapped('test_parameter')))

        # 3. GATE: "Present" pathogens require remarks to pass
        present_lines = self.microbiological_line_ids.filtered(
            lambda l: l.detection_result == 'present')
        present_no_remarks = present_lines.filtered(lambda l: not l.remarks)
        if present_no_remarks:
            errors.append(_(
                "❌ The following pathogens are PRESENT but have no remarks:\n• %s"
            ) % '\n• '.join(present_no_remarks.mapped('test_parameter')))

        # 4. Line-level validation (standard + micro problems together)
        errors += self._collect_validation_errors(all_lines, 'Pass')

        # RAISE ONE SINGLE ERROR LISTING EVERY PROBLEM
        if errors:
            raise UserError(_(
                "QC Validation Error:\n\n%s\n\n"
                "Please fix all the above issues before proceeding."
            ) % '\n\n'.join(errors))

        self.write({'state': 'passed'})

        # Audit trail for forced passes
        if present_lines:
            self.message_post(body=_(
                "<b>FORCED PASS:</b> QC passed with pathogen(s) marked PRESENT: %s. "
                "See remarks for justification."
            ) % ', '.join(present_lines.mapped('test_parameter')))

    def action_fail(self):
        self.ensure_one()
        all_lines = self.standard_line_ids | self.microbiological_line_ids
        if not all_lines:
            raise UserError(_("No test parameters found. Please load parameters before failing."))

        errors = self._collect_validation_errors(all_lines, 'Fail')

        failed_lines = all_lines.filtered(lambda l: l.status == 'fail')
        if not failed_lines:
            errors.append(_(
                "❌ This Quality Check cannot be failed because ALL test "
                "parameters have Passed (or are N/A)."
            ))

        if errors:
            raise UserError(_("QC Validation Error:\n\n%s") % '\n\n'.join(errors))

        self.write({'state': 'failed'})
        self.message_post(body=_(
            "<b>BATCH ON HOLD / DISCARDED.</b><br/>"
            "The Finished Goods QC has FAILED."
        ))

    def _load_default_parameters(self):
        """Splits fg_specs into standard and microbiological lines on load."""
        self.ensure_one()
        if not self.product_id:
            return

        standard_lines = []
        microbiological_lines = []

        for param_line in self.product_id.product_tmpl_id.fg_specs:
            is_micro = self._is_micro_param(param_line.parameter_id.name)  # tolerant match

            line_vals = {
                'parameter_id': param_line.parameter_id.id,
                'specification': param_line.specification or param_line.parameter_id.default_specification,
                'condition': param_line.condition or param_line.parameter_id.default_condition,
            }

            if is_micro:
                microbiological_lines.append((0, 0, line_vals))
            else:
                standard_lines.append((0, 0, line_vals))

        if standard_lines:
            self.standard_line_ids = standard_lines
        if microbiological_lines:
            self.microbiological_line_ids = microbiological_lines

    # ==========================================
    # SEQUENCE & CRUD
    # ==========================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('finished.qc') or _('New')
        return super().create(vals_list)


class FinishedQCLine(models.Model):
    _name = 'finished.qc.line'
    _description = 'Finished Goods QC Test Line'
    _order = 'sequence, id'

    # Two separate inverse fields: a line belongs to exactly ONE tab.
    standard_qc_id = fields.Many2one(
        'finished.qc', string='QC Document', ondelete='cascade', index=True)
    micro_qc_id = fields.Many2one(
        'finished.qc', string='QC Document (Micro)', ondelete='cascade', index=True)

    sequence = fields.Integer(string='Sequence', default=10)

    parameter_id = fields.Many2one('kalsal.quality.parameter', string='Test Parameter')
    test_parameter = fields.Char(related='parameter_id.name', string='Test Parameter Name', store=True)

    specification = fields.Char(string='Specification / Limit')
    condition = fields.Selection([
        ('nmt', 'Not More Than'),
        ('nlt', 'Not Less Than'),
    ], string='Condition')

    result = fields.Char(string='Result')

    # Mandatory explicit choice: Absent (Pass) / Present (Fail, needs remarks to pass)
    detection_result = fields.Selection([
        ('absent', 'Absent'),
        ('present', 'Present'),
    ], string='Detection Result', tracking=True)

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

    @api.onchange('detection_result')
    def _onchange_detection_result(self):
        """Keep the text Result in sync for traceability / reports."""
        for rec in self:
            if rec.detection_result == 'present':
                rec.result = 'Present'
            elif rec.detection_result == 'absent':
                rec.result = 'Absent'
            else:
                rec.result = False

    @api.depends('result', 'specification', 'condition', 'detection_result')
    def _compute_status(self):
        for rec in self:
            # Micro lines: driven purely by the mandatory detection choice
            if rec.micro_qc_id:
                if not rec.detection_result:
                    rec.status = 'pending'
                else:
                    rec.status = 'fail' if rec.detection_result == 'present' else 'pass'
                continue

            # Standard lines: numeric engine
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

    def _expected_status(self):
        self.ensure_one()
        if self.micro_qc_id:
            if not self.detection_result:
                return False
            return 'fail' if self.detection_result == 'present' else 'pass'

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
        clean_str = str(value_str).strip().replace(',', '')
        match = re.search(r'[-+]?(?:\d*\.\d+|\d+)', clean_str)
        if match:
            try:
                return float(match.group())
            except ValueError:
                return None
        return None
