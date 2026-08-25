from datetime import datetime
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class KalsalReworkSheet(models.Model):
    _name = 'kalsal.rework.sheet'
    _description = 'Rework Check Sheet'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # ==========================================
    # HEADER & ANCHOR
    # ==========================================
    name = fields.Char(
        string='Rework Sheet No', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True)

    semi_finished_qc_id = fields.Many2one(
        'semi.finished.qc', string='Failed Semi-Finished QC',
        domain="[('state', '=', 'failed'), ('is_post_rework_qc', '=', False)]",
        tracking=True,
        help="Select the failed QC record that requires reworking. "
             "Post-Rework QCs are excluded: a batch that already went "
             "through rework and failed again must be discarded.")

    # Guard: never allow selecting a Post-Rework QC for a second rework
    @api.constrains('semi_finished_qc_id')
    def _check_no_second_rework(self):
        for rec in self:
            if rec.semi_finished_qc_id and rec.semi_finished_qc_id.is_post_rework_qc:
                raise UserError(_(
                    "Rework Validation Error:\n"
                    "QC %s is a Post-Rework QC. A batch that has already "
                    "been reworked and failed again must be DISCARDED — "
                    "a second rework is strictly prohibited."
                ) % rec.semi_finished_qc_id.name)

    # ==========================================
    # NEW: REWORK LIMIT (count-based, simple & reliable)
    # 1 failed QC  = first failure      -> rework allowed
    # 2 failed QCs = reworked & failed  -> DISCARDED
    # ==========================================
    def _get_failed_qc_count(self):
        self.ensure_one()
        if not (self.sale_order_id and self.product_id):
            return 0
        return self.env['semi.finished.qc'].search_count([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('product_id', '=', self.product_id.id),
            ('state', '=', 'failed'),
        ])

    @api.constrains('semi_finished_qc_id')
    def _check_rework_limit(self):
        """Block generating a rework sheet when the batch is already discarded."""
        for rec in self:
            if not rec.semi_finished_qc_id:
                continue
            failed_count = self.env['semi.finished.qc'].search_count([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('product_id', '=', rec.product_id.id),
                ('state', '=', 'failed'),
            ])
            if failed_count >= 2:
                raise UserError(_(
                    "Rework Blocked:\n"
                    "SO %s / %s already has %s failed QCs (original + post-rework). "
                    "This batch was reworked once and failed again — it must be "
                    "DISCARDED. A second rework is strictly prohibited."
                ) % (rec.sale_order_id.name, rec.product_id.display_name, failed_count))

    # ==========================================
    # AUTO-FETCHED FIELDS (ReadOnly)
    # ==========================================
    product_id = fields.Many2one(
        'product.product', string='Product Name',
        related='semi_finished_qc_id.product_id', store=True, readonly=True)

    batch_no = fields.Many2one(
        'stock.lot', string='Product Batch No',
        related='semi_finished_qc_id.batch_no', store=True, readonly=True)

    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order',
        related='semi_finished_qc_id.sale_order_id', store=True, readonly=True)

    # ==========================================
    # POST-REWORK QC LINK (One-Way)
    # ==========================================
    post_rework_qc_id = fields.Many2one(
        'semi.finished.qc', string='Post-Rework QC',
        readonly=True, copy=False, tracking=True)

    post_rework_qc_count = fields.Integer(
        string='Post-Rework QC Count', compute='_compute_post_rework_qc_count')

    # ==========================================
    # DATES (Editable with defaults)
    # ==========================================
    production_date = fields.Date(
        string='Production Date',
        default=fields.Date.context_today,
        tracking=True)

    expiry_date = fields.Date(
        string='Expiry Date',
        default=lambda self: datetime(2027, 1, 1).date(),
        tracking=True)

    # ==========================================
    # MANUAL ENTRY FIELDS
    # ==========================================
    rework_qty = fields.Float(string='Rework Quantity', tracking=True)
    freshly_produced_qty = fields.Float(string='Freshly Produced Quantity', tracking=True)
    actual_rework_pct = fields.Float(string='Actual Rework Added %', tracking=True)
    quantity_added = fields.Float(string='Quantity Added', tracking=True)
    total_batch_qty = fields.Float(string='Total Batch Quantity', tracking=True)

    # ==========================================
    # SIGN-OFF FIELDS (Always visible, auto-filled)
    # ==========================================
    checked_by = fields.Many2one(
        'res.users', string='Checked By',
        default=lambda self: self.env.user, tracking=True)

    verified_by_qc = fields.Many2one(
        'res.users', string='Verified By QC',
        default=lambda self: self.env.user, tracking=True)

    # ==========================================
    # STATE
    # ==========================================
    state = fields.Selection([
        ('draft', 'Draft'),
        ('passed', 'Passed'),
    ], string='Status', default='draft', tracking=True)

    # ==========================================
    # COMPUTES
    # ==========================================
    def _compute_post_rework_qc_count(self):
        for rec in self:
            rec.post_rework_qc_count = 1 if rec.post_rework_qc_id else 0

    # ==========================================
    # SEQUENCE
    # ==========================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'rework.sheet') or _('New')
        return super().create(vals_list)

    # ==========================================
    # ACTIONS
    # ==========================================
    def action_confirm_rework(self):
        """Lock the sheet as Passed and auto-redirect to Post-Rework QC."""
        for rec in self:
            # HARD GATE: only ONE rework allowed per SO + Product
            failed_count = rec._get_failed_qc_count()
            if failed_count >= 2:
                raise UserError(_(
                    "Rework Blocked:\n"
                    "SO %s / %s already has %s failed QCs. This batch was "
                    "reworked once and failed again — it must be DISCARDED."
                ) % (rec.sale_order_id.name, rec.product_id.display_name, failed_count))

            rec.write({'state': 'passed'})
            rec.message_post(body=_("<b>Rework Check Sheet Passed.</b>"))

            # If a Post-Rework QC already exists, just get its action
            if rec.post_rework_qc_id:
                action = {
                    'type': 'ir.actions.act_window',
                    'name': _('Post-Rework QC'),
                    'res_model': 'semi.finished.qc',
                    'res_id': rec.post_rework_qc_id.id,
                    'view_mode': 'form',
                    'target': 'current',
                }
            else:
                # Create a new one and get its action
                qc_vals = {
                    'sale_order_id': rec.sale_order_id.id if rec.sale_order_id else False,
                    'product_id': rec.product_id.id if rec.product_id else False,
                    'batch_no': rec.batch_no.id if rec.batch_no else False,
                }

                new_qc = self.env['semi.finished.qc'].create(qc_vals)
                rec.write({'post_rework_qc_id': new_qc.id})

                rec.message_post(body=_(
                    "<b>Post-Rework QC Created:</b> %s" % new_qc.name
                ))

                action = {
                    'type': 'ir.actions.act_window',
                    'name': _('New Post-Rework QC'),
                    'res_model': 'semi.finished.qc',
                    'res_id': new_qc.id,
                    'view_mode': 'form',
                    'target': 'current',
                }

            # Return the action of the last processed record
            return action

    def action_view_failed_qc(self):
        """Smart button: Open the linked Failed QC form."""
        self.ensure_one()
        if not self.semi_finished_qc_id:
            raise UserError(_('This rework sheet is not linked to any Semi-Finished QC.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Failed Semi-Finished QC'),
            'res_model': 'semi.finished.qc',
            'res_id': self.semi_finished_qc_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_create_or_view_post_rework_qc(self):
        """Smart button: Create a fresh Post-Rework QC or open the existing one."""
        self.ensure_one()

        if self.post_rework_qc_id:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Post-Rework QC'),
                'res_model': 'semi.finished.qc',
                'res_id': self.post_rework_qc_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

        qc_vals = {
            'sale_order_id': self.sale_order_id.id if self.sale_order_id else False,
            'product_id': self.product_id.id if self.product_id else False,
            'batch_no': self.batch_no.id if self.batch_no else False,
        }

        new_qc = self.env['semi.finished.qc'].create(qc_vals)
        self.write({'post_rework_qc_id': new_qc.id})

        self.message_post(body=_(
            "<b>Post-Rework QC Created:</b> %s" % new_qc.name
        ))

        return {
            'type': 'ir.actions.act_window',
            'name': _('New Post-Rework QC'),
            'res_model': 'semi.finished.qc',
            'res_id': new_qc.id,
            'view_mode': 'form',
            'target': 'current',
        }


# ==========================================
# INHERIT SFG-QC FOR LIFECYCLE RULES & DISCARD BANNER
# ==========================================
class SemiFinishedQCInherit(models.Model):
    _inherit = 'semi.finished.qc'

    # ==========================================
    # DISCARD RULE FLAG
    # ==========================================
    is_post_rework_qc = fields.Boolean(
        string='Is Post-Rework QC',
        compute='_compute_is_post_rework_qc',
        search='_search_is_post_rework_qc',
        help="True when this QC was generated from a Rework Sheet. "
             "If a Post-Rework QC fails, the batch is discarded.")

    def _compute_is_post_rework_qc(self):
        for rec in self:
            rec.is_post_rework_qc = bool(self.env['kalsal.rework.sheet'].search_count([
                ('post_rework_qc_id', '=', rec.id),
            ]))

    def _search_is_post_rework_qc(self, operator, value):
        """Makes the computed flag usable inside domains."""
        qc_ids = self.env['kalsal.rework.sheet'].search([
            ('post_rework_qc_id', '!=', False),
        ]).mapped('post_rework_qc_id').ids
        is_true = value in (True, 'true', 'True', 1)
        if (operator == '=' and is_true) or (operator == '!=' and not is_true):
            return [('id', 'in', qc_ids)]
        return [('id', 'not in', qc_ids)]

    # ==========================================
    # DROPDOWN FILTER: Hide Failed/Discarded Products
    # ==========================================

    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        """Override base compute to hide products locked in the rework lifecycle
        AND ensure their mixing process is marked as DONE."""
        for rec in self:
            if not rec.sale_order_id:
                rec.allowed_recipe_product_ids = False
                continue

            # 1. Get products on the SO
            so_products = rec.sale_order_id.order_line.mapped('product_id')

            # 2. Find products that have a completed Mixing Slip
            mixed_products = self.env['mixing.slip'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', '=', 'done')
            ]).mapped('recipe_product_id')

            # 3. Intersect: Mixing MUST be done AND product MUST NOT be blocked by rework lifecycle
            rec.allowed_recipe_product_ids = so_products.filtered(
                lambda p: p in mixed_products and not rec._is_product_qc_blocked(p)
            )

    def _is_product_qc_blocked(self, product):
        """Block a NEW manual QC for this product when:
        1. DISCARDED      : a failed Post-Rework QC exists (reworked & failed again).
        2. OPEN QC        : a draft/in-progress QC already exists (no parallel QCs).
        3. REWORK PENDING : a failed 1st QC exists and no Post-Rework QC was generated yet.
        """
        self.ensure_one()
        qcs = self.search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('product_id', '=', product.id),
        ])
        if not qcs:
            return False

        post_rework_qcs = qcs.filtered('is_post_rework_qc')

        # 1. Discarded batch -> never again (ignore if THIS is the discarded record)
        if post_rework_qcs.filtered(lambda q: q.state == 'failed' and q.id != self.id):
            return True

        # 2. An active QC already exists -> no parallel/manual QC (ignore if THIS is the active one)
        if qcs.filtered(lambda q: q.state in ('draft', 'in_progress') and q.id != self.id):
            return True

        # 3. Failed 1st QC whose rework is not completed yet (ignore if THIS is the failed one)
        failed_base = qcs.filtered(lambda q: q.state == 'failed' and not q.is_post_rework_qc and q.id != self.id)
        if failed_base and not post_rework_qcs:
            return True

        return False

    # NOTE: The old _check_discarded_batch_creation constraint has been REMOVED.
    # It caused false "DISCARDED" blocks during Post-Rework QC creation.
    # The rework limit is now enforced by the simple failed-QC COUNT in
    # KalsalReworkSheet._check_rework_limit + action_confirm_rework.

    def action_fail(self):
        res = super().action_fail()
        for rec in self:
            if rec.is_post_rework_qc:
                # Second failure -> DISCARD (no rework sheet, no new QC ever)
                rec.message_post(body=_(
                    "<b>BATCH DISCARDED.</b><br/>"
                    "The Post-Rework QC has FAILED. This batch has already "
                    "been reworked once and will NOT be sent for a second "
                    "rework — it must be discarded."
                ))
            else:
                # First failure -> auto-create the Rework Sheet and REDIRECT
                sheet = self.env['kalsal.rework.sheet'].search([
                    ('semi_finished_qc_id', '=', rec.id),
                ], limit=1)
                if not sheet:
                    sheet = self.env['kalsal.rework.sheet'].create({
                        'semi_finished_qc_id': rec.id,
                    })
                    rec.message_post(body=_(
                        "<b>Rework Check Sheet Created:</b> %s" % sheet.name))
                return {
                    'type': 'ir.actions.act_window',
                    'name': _('Rework Check Sheet'),
                    'res_model': 'kalsal.rework.sheet',
                    'res_id': sheet.id,
                    'view_mode': 'form',
                    'target': 'current',
                }
        return res
