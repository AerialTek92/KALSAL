from odoo import models, fields, api, _
from odoo.exceptions import UserError


# ==========================================
# 1. EXTEND GLOBAL PARAMETER (add detection to default_condition)
# ==========================================
class KalsalQualityParameterInherit(models.Model):
    _inherit = 'kalsal.quality.parameter'

    default_condition = fields.Selection(
        selection_add=[('detection', 'Detection Test (Satisfactory / Non-Satisfactory)')],
        ondelete={'detection': 'set null'},  # ← FIXED
    )


# ==========================================
# 2. EXTEND RAW SPEC LINE (product tab)
# ==========================================
class ProductQualityParameterLineInherit(models.Model):
    _inherit = 'product.quality.parameter.line'

    is_microbiological = fields.Boolean(
        string='Microbiological',
        help="Tick to route this parameter into the Microbiological Tests tab "
             "of the Raw Material QC. Unticked parameters stay in the "
             "standard Test Parameters tab.")

    condition = fields.Selection(
        selection_add=[('detection', 'Detection Test (Satisfactory / Non-Satisfactory)')],
        ondelete={'detection': 'set null'},  # ← FIXED
    )


# ==========================================
# 3. EXTEND QC DOCUMENT
# ==========================================
class KalsalQualityCheckInherit(models.Model):
    _inherit = 'kalsal.quality.check'

    # Tighten existing O2M domains to exclude micro lines
    first_qc_line_ids = fields.One2many(
        'kalsal.quality.check.line',
        'check_id',
        string='1st QC Parameters',
        domain=[('delay_required', '=', False), ('is_microbiological', '=', False)]
    )

    second_qc_line_ids = fields.One2many(
        'kalsal.quality.check.line',
        'check_id',
        string='2nd QC Parameters',
        domain=[('delay_required', '=', True), ('is_microbiological', '=', False)]
    )

    # NEW: Micro O2M fields with combined domains
    first_qc_micro_line_ids = fields.One2many(
        'kalsal.quality.check.line',
        'check_id',
        string='1st QC Micro Parameters',
        domain=[('delay_required', '=', False), ('is_microbiological', '=', True)]
    )

    second_qc_micro_line_ids = fields.One2many(
        'kalsal.quality.check.line',
        'check_id',
        string='2nd QC Micro Parameters',
        domain=[('delay_required', '=', True), ('is_microbiological', '=', True)]
    )

    # Compute booleans for tab visibility
    has_1st_phase_micro = fields.Boolean(
        string='Has 1st Phase Micro',
        compute='_compute_has_micro_phases',
        store=True,
    )

    has_2nd_phase_micro = fields.Boolean(
        string='Has 2nd Phase Micro',
        compute='_compute_has_micro_phases',
        store=True,
    )

    has_detection_lines = fields.Boolean(
        string='Has Detection Parameters',
        compute='_compute_has_detection_lines',
        help="True when at least one test line of this QC uses a detection condition.")

    @api.depends('check_line_ids.condition')
    def _compute_has_detection_lines(self):
        for rec in self:
            rec.has_detection_lines = bool(
                rec.check_line_ids.filtered(lambda l: l.condition == 'detection'))

    show_micro_tab = fields.Boolean(
        string='Show Microbiological Tab',
        compute='_compute_show_micro_tab')

    @api.depends('state', 'has_1st_phase_micro', 'has_2nd_phase_micro')
    def _compute_show_micro_tab(self):
        """The Micro tab exists ONLY when the CURRENT phase actually has
        micro parameters — and never at all if the product has none."""
        for rec in self:
            if rec.state == '1st_in_progress':
                rec.show_micro_tab = rec.has_1st_phase_micro
            elif rec.state in ('waiting_delay', '2nd_in_progress'):
                rec.show_micro_tab = rec.has_2nd_phase_micro
            else:  # draft / pass / fail / cancel → full record view
                rec.show_micro_tab = (
                        rec.has_1st_phase_micro or rec.has_2nd_phase_micro)

    @api.depends('check_line_ids.is_microbiological', 'check_line_ids.delay_required')
    def _compute_has_micro_phases(self):
        for rec in self:
            rec.has_1st_phase_micro = bool(rec.check_line_ids.filtered(
                lambda l: l.is_microbiological and not l.delay_required))
            rec.has_2nd_phase_micro = bool(rec.check_line_ids.filtered(
                lambda l: l.is_microbiological and l.delay_required))

    def _load_default_test_lines(self):
        """Extend to carry is_microbiological and detection condition from product spec."""
        for rec in self:
            if rec.check_line_ids or not rec.product_id:
                continue
            tmpl = rec.product_id.product_tmpl_id
            type_key = tmpl.product_type_custom
            match type_key:
                case 'raw':
                    qc = tmpl.quality_param_line_ids
                case 'packaging':
                    qc = tmpl.packaging_material_specs
                case _:
                    selection_dict = dict(tmpl._fields['product_type_custom'].selection)
                    type_label = selection_dict.get(type_key, type_key)
                    raise UserError(_(
                        "Only Raw Material & Packaging Materials are Eligible for this Quality Check. "
                        "Product '%s' is set as '%s' in the Product Description Page."
                    ) % (tmpl.display_name, type_label))

            if qc:
                lines_to_create = []
                seq = 10
                for param_line in qc:
                    lines_to_create.append((0, 0, {
                        'sequence': seq,
                        'test_parameter': param_line.parameter_id.name,
                        'condition': param_line.condition or param_line.parameter_id.default_condition,
                        'specification': param_line.specification or param_line.parameter_id.default_specification,
                        'delay_required': param_line.delay_required,
                        # FIX: Use getattr to safely default to False for models
                        # that don't have the micro field (like packaging)
                        'is_microbiological': getattr(param_line, 'is_microbiological', False),
                        'status': 'pending',
                    }))
                    seq += 10
                if lines_to_create:
                    rec.check_line_ids = lines_to_create

    def _validate_qc_lines(self, lines, phase_label, result):
        """Extend with detection gates."""
        # Call super first to run existing validations
        super()._validate_qc_lines(lines, phase_label, result)

        if not lines:
            return

        # NEW: Detection choice mandatory
        unselected = lines.filtered(
            lambda l: l.condition == 'detection' and not l.detection_result)
        if unselected:
            raise UserError(_(
                "%s Validation Error:\n"
                "The following detection tests must select 'Satisfactory' or 'Non-Satisfactory':\n\n• %s"
            ) % (phase_label, '\n• '.join(unselected.mapped('test_parameter'))))

        # NEW: Non-satisfactory detection requires remarks to pass
        if result == "Pass":
            non_sat_no_remarks = lines.filtered(
                lambda l: l.condition == 'detection'
                          and l.detection_result == 'non_satisfactory'
                          and not l.remarks
            )
            if non_sat_no_remarks:
                raise UserError(_(
                    "%s Validation Error:\n"
                    "The following detection tests are NON-SATISFACTORY but have no remarks. "
                    "Remarks are mandatory to force-pass:\n\n• %s"
                ) % (phase_label, '\n• '.join(non_sat_no_remarks.mapped('test_parameter'))))

    def action_pass(self):
        """Extend to audit post forced passes with non_satisfactory detection."""
        for rec in self:
            # Capture detection lines before calling existing logic
            all_lines = rec.check_line_ids
            non_sat_lines = all_lines.filtered(
                lambda l: l.condition == 'detection' and l.detection_result == 'non_satisfactory'
            )

            # Call existing pass logic via super
            super(KalsalQualityCheckInherit, rec).action_pass()

            # NEW: Audit post for forced passes
            if non_sat_lines:
                rec.message_post(body=_(
                    "<b>FORCED PASS:</b> QC passed with detection test(s) marked NON-SATISFACTORY: %s. "
                    "See remarks for justification."
                ) % ', '.join(non_sat_lines.mapped('test_parameter')))


# ==========================================
# 4. EXTEND QC LINE
# ==========================================
class KalsalQualityCheckLineInherit(models.Model):
    _inherit = 'kalsal.quality.check.line'

    is_microbiological = fields.Boolean(
        string='Microbiological',
        store=True,
        help="Routed to the Microbiological Tests tab if ticked.")

    condition = fields.Selection(
        selection_add=[('detection', 'Detection Test (Satisfactory / Non-Satisfactory)')],
        ondelete={'detection': 'set null'},  # ← FIXED
    )

    detection_result = fields.Selection([
        ('satisfactory', 'Satisfactory'),
        ('non_satisfactory', 'Non-Satisfactory'),
    ], string='Detection Result', tracking=True)

    @api.onchange('detection_result')
    def _onchange_detection_result(self):
        """Sync text result field for traceability."""
        for rec in self:
            if rec.detection_result == 'satisfactory':
                rec.result = 'Satisfactory'
            elif rec.detection_result == 'non_satisfactory':
                rec.result = 'Non-Satisfactory'
            else:
                rec.result = False

    @api.depends('result', 'specification', 'condition', 'detection_result')
    def _compute_status(self):
        """Extend to handle detection lines."""
        for rec in self:
            # DETECTION TESTS: driven by the mandatory detection choice
            if rec.condition == 'detection':
                if not rec.detection_result:
                    rec.status = 'pending'
                else:
                    rec.status = 'fail' if rec.detection_result == 'non_satisfactory' else 'pass'
                continue

            # NUMERIC TESTS: existing NMT / NLT logic (fallback to super or base logic)
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
        """Helper for forced-pass detection."""
        self.ensure_one()
        if self.condition == 'detection':
            if not self.detection_result:
                return False
            return 'fail' if self.detection_result == 'non_satisfactory' else 'pass'

        target_val = self._parse_to_float(self.specification)
        actual_val = self._parse_to_float(self.result)
        if target_val is None or actual_val is None:
            return False
        if self.condition == 'nlt':
            return 'pass' if actual_val >= target_val else 'fail'
        if self.condition == 'nmt':
            return 'pass' if actual_val <= target_val else 'fail'
        return False
