from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup
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

    # NEW (derived from SO line's BOM):
    semi_product_id = fields.Many2one(
        'product.product', string='Semi-Finished (Bulk) Product',
        compute='_compute_semi_product_id', store=True, readonly=True,
        help="Derived from the Sale Order line's chosen Recipe Version: "
             "the component in that BOM with Category Type = Semi-Finished.")

    product_category = fields.Many2many(
        'recipe.category', string='Recipe Category',
        related='product_id.product_tmpl_id.recipe_category_ids',
        readonly=True, store=False,
        help="Decided on the product master - read-only here.")

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

    batch_no = fields.Many2many(
        'stock.lot', string='Batch / Lot No',
        tracking=True,
        help="Auto-fetched from Manufacturing Orders based on Sale Order and Product.")

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

    has_detection_lines = fields.Boolean(compute='_compute_has_detection_lines')

    @api.depends('line_ids.condition')
    def _compute_has_detection_lines(self):
        for rec in self:
            rec.has_detection_lines = bool(
                rec.line_ids.filtered(lambda l: l.condition == 'detection'))

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
        Operations users."""
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

    def _notify_production_users_semi_qc_passed(self):
        self.ensure_one()
        target_partners = self._get_group_partners('fk_kalsal_security.group_executor_production')
        if not target_partners:
            return

        base_url = self._get_current_base_url()
        # Join multiple batch names with commas
        batch_name = ', '.join(self.batch_no.mapped('name')) if self.batch_no else _('N/A')

        action_vals = {
            'name': _('New FG Reporting - Batch %s') % batch_name,
            'res_model': 'fg.reporting',
            'view_mode': 'form',
            'target': 'current',
        }
        if self.sale_order_id:
            action_vals['context'] = "{'default_sale_order_id': %d}" % self.sale_order_id.id
        action = self.env['ir.actions.act_window'].sudo().create(action_vals)
        fg_new_url = f"{base_url}/web#action={action.id}"

        title = _("Semi-Finished QC Passed")
        plain_msg = _(
            "Quality for batch %s is done. Kindly create an FG Report to transfer that batch to Store."
        ) % batch_name
        html_msg = Markup(_(
            "Quality for batch <b>%s</b> is done.<br/><br/>"
            "Kindly create an FG Report to transfer that batch to Store. "
            "<a href='%s' target='_blank'><b>Click here to create a new FG Report</b></a>."
        )) % (batch_name, fg_new_url)

        notify_ctx = dict(self.env.context, mail_notify_force_send=False)
        if hasattr(self, 'message_notify'):
            self.with_context(notify_ctx).message_notify(
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        else:
            self.env['mail.thread'].with_context(notify_ctx).message_notify(
                model='semi.finished.qc', res_id=self.id,
                partner_ids=target_partners.ids, body=html_msg, subject=title
            )
        for partner in target_partners:
            self.env['bus.bus']._sendone(
                partner, 'simple_notification',
                {'type': 'success', 'title': title, 'message': plain_msg, 'sticky': True}
            )

    @api.depends('sale_order_id', 'product_id')
    def _compute_semi_product_id(self):
        """Find the semi-finished bulk product from the SO line's chosen
        Recipe Version (bom_id.bom_line_ids filtered by product_type_custom)."""
        for rec in self:
            if not (rec.sale_order_id and rec.product_id):
                rec.semi_product_id = False
                continue

            so_line = rec.sale_order_id.order_line.filtered(
                lambda l: l.product_id == rec.product_id)[:1]
            if not so_line or not so_line.bom_id:
                rec.semi_product_id = False
                continue

            semi = so_line.bom_id.bom_line_ids.mapped('product_id').filtered(
                lambda p: p.product_tmpl_id.product_type_custom == 'semi')[:1]
            rec.semi_product_id = semi.id if semi else False

    def _compute_allowed_sale_order_ids(self):
        """SO becomes selectable as soon as one of its Mixing Slips is DONE
        AND it has mixed-but-not-QC'd quantity available.
        Line Clearance no longer gates this dropdown."""
        for rec in self:
            done_slips = self.env['mixing.slip'].search([
                ('state', '=', 'done'),
                ('sale_order_id', '!=', False),
            ])
            candidate_sos = done_slips.mapped('sale_order_id')

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

    @api.depends('semi_product_id')
    def _compute_allowed_color_ids(self):
        """Fetch colors defined on the selected product's Colors tab"""
        for rec in self:
            if rec.semi_product_id:
                rec.allowed_color_ids = rec.semi_product_id.product_tmpl_id.color_parameter_ids
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
            self.semi_product_id = False
            self.batch_no = [(5, 0, 0)]  # Clear Many2many
            self.line_ids = [(5, 0, 0)]
            self.qty_to_qc = 0.0

    @api.onchange('product_id')
    def _onchange_product_id_fetch_batch(self):
        """Fetch all Batches/Lots from matching MOs, auto-load parameters, and lock the QC quantity."""
        batch_ids = []
        if self.sale_order_id and self.product_id:
            so_name = self.sale_order_id.name
            # Fetch ALL MOs for this SO/Product (supports multi-batch runs)
            mos = self.env['mrp.production'].search([
                ('origin', 'ilike', f'%{so_name}%'),
                ('product_id', '=', self.product_id.id),
                ('state', 'in', ('done', 'to_close', 'progress')),
            ], order='id desc')

            # Collect all lots from all matching MOs
            for mo in mos:
                if mo.lot_producing_ids:
                    batch_ids.extend(mo.lot_producing_ids.ids)

        # Fallback 1: if no MO lots found, grab lots that currently have stock in Production locations
        if not batch_ids and self.product_id:
            prod_locs = self.env['stock.location'].search([
                ('usage', '=', 'production'),
                '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False)
            ])
            if prod_locs:
                available_quants = self.env['stock.quant'].search([
                    ('product_id', '=', self.product_id.id),
                    ('location_id', 'in', prod_locs.ids),
                    ('quantity', '>', 0)
                ])
                batch_ids = available_quants.mapped('lot_id').ids

            # Fallback 2: Ultimate fallback, grab all lots (will be strictly filtered below)
            if not batch_ids:
                all_lots = self.env['stock.lot'].search([
                    ('product_id', '=', self.product_id.id),
                    ('company_id', '=', self.env.company.id)
                ], order='id desc')
                batch_ids = all_lots.ids

        # CRITICAL FIX: Exclude lots that have already been processed (Passed/Failed)
        # in ANY Semi-Finished QC. This prevents grabbing old "Finished QC" batches.
        consumed_lots = self.search([('state', 'in', ('passed', 'failed'))]).mapped('batch_no')
        consumed_lot_ids = set(consumed_lots.ids)

        # Also exclude lots currently held by ANOTHER open (draft/in_progress) SFG QC
        open_qcs = self.search([('state', 'in', ('draft', 'in_progress'))])
        if self.id and isinstance(self.id, int):
            open_qcs = open_qcs.filtered(lambda q: q.id != self.id)
        open_lot_ids = set(open_qcs.mapped('batch_no').ids)

        excluded_ids = consumed_lot_ids | open_lot_ids
        batch_ids = [bid for bid in batch_ids if bid not in excluded_ids]

        # Assign as Many2many (using set to remove duplicates)
        self.batch_no = [(6, 0, list(set(batch_ids)))]

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

        # Lock the quantity as soon as the product is selected
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

        # NEW GATE 1: detection tests must have an explicit choice
        unselected = lines.filtered(
            lambda l: l.condition == 'detection' and not l.detection_result)
        if unselected:
            raise UserError(_(
                "QC Validation Error:\n"
                "The following detection tests must select 'Satisfactory' or "
                "'Non-Satisfactory':\n\n• %s"
            ) % '\n• '.join(_label(l) for l in unselected))

        incomplete_lines = lines.filtered(
            lambda l: l.status == 'pending'
                      or (not l.result and l.status != 'na'
                          and l.condition != 'detection')  # NEW: detection exempt
        )
        if incomplete_lines:
            raise UserError(_(
                "QC Validation Error:\n"
                "The following test parameters are missing either a result "
                "or a status:\n\n• %s"
            ) % '\n• '.join(_label(l) for l in incomplete_lines))

        if result == 'Pass':
            # Unchanged: a Non-Satisfactory detection line has status 'fail',
            # so this existing gate already forces remarks for it.
            failed_no_remarks = lines.filtered(
                lambda l: l.status == 'fail' and not l.remarks)
            if failed_no_remarks:
                raise UserError(_(
                    "QC Validation Error:\n"
                    "The following parameters are marked as FAILED but have "
                    "no remarks:\n\n• %s"
                ) % '\n• '.join(_label(l) for l in failed_no_remarks))

            # Unchanged: thanks to 2d, a detection line manually forced to
            # 'pass' while Non-Satisfactory is caught here and needs remarks.
            forced_pass_no_remarks = lines.filtered(
                lambda l: l.status == 'pass'
                          and l._expected_numeric_status() == 'fail'
                          and not l.remarks)
            if forced_pass_no_remarks:
                raise UserError(_(
                    "QC Validation Error:\n"
                    "The following parameters EXCEED their specification "
                    "limit but are manually marked as PASS. Please add "
                    "remarks justifying this manual override:\n\n• %s"
                ) % '\n• '.join(_label(l) for l in forced_pass_no_remarks))

    def action_pass(self):
        self.ensure_one()

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
            mos = self.env['mrp.production'].search([
                ('origin', 'ilike', f'%{self.sale_order_id.name}%'),
                ('product_id', '=', self.product_id.id),
                ('state', 'in', ('done', 'to_close')),
            ])

            # Only DONE MOs may carry lots. Pending backorders stay lot-less
            # until their own mixing run names them after their MO.
            for mo in mos.filtered(lambda m: not m.lot_producing_ids):
                lot = self.env['stock.lot'].create({
                    'product_id': mo.product_id.id,
                    'company_id': mo.company_id.id,
                    'name': mo.name,
                })
                mo.lot_producing_ids = [(6, 0, lot.ids)]

            all_lots = mos.mapped('lot_producing_ids')
            if all_lots and not self.batch_no:
                self.batch_no = [(6, 0, all_lots.ids)]

        self._notify_production_users_semi_qc_passed()

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
        if not self.semi_product_id:
            return

        lines = []
        for param_line in self.semi_product_id.product_tmpl_id.semi_fg_specs:
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
            if not rec.semi_product_id:
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
        ('detection', 'Detection Test (Satisfactory / Non-Satisfactory)'),
    ], string='Condition')

    detection_result = fields.Selection([
        ('satisfactory', 'Satisfactory'),
        ('non_satisfactory', 'Non-Satisfactory'),
    ], string='Detection Result', tracking=True)

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

    @api.onchange('detection_result')
    def _onchange_detection_result(self):
        """Sync the text result for traceability, same as Raw/FG QC."""
        for rec in self:
            if rec.detection_result == 'satisfactory':
                rec.result = 'Satisfactory'
            elif rec.detection_result == 'non_satisfactory':
                rec.result = 'Non-Satisfactory'
            else:
                rec.result = False

    @api.depends('result', 'specification', 'condition', 'detection_result')
    def _compute_status(self):
        for rec in self:
            if rec.condition == 'detection':
                if not rec.detection_result:
                    rec.status = 'pending'
                else:
                    rec.status = 'fail' if rec.detection_result == 'non_satisfactory' else 'pass'
                continue

            # --- existing numeric logic below, unchanged ---
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
        if self.condition == 'detection':
            if not self.detection_result:
                return False
            return 'fail' if self.detection_result == 'non_satisfactory' else 'pass'

        # --- existing numeric logic below, unchanged ---
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


# ==========================================
# DETECTION CONDITION FOR SEMI-FINISHED SPECS
# ==========================================
class SemiQualityParameterLineInherit(models.Model):
    _inherit = 'semi.quality.parameter.line'  # ← REPLACE with Step 0 output

    condition = fields.Selection(
        selection_add=[('detection', 'Detection Test (Satisfactory / Non-Satisfactory)')],
        ondelete={'detection': 'set null'},
    )
