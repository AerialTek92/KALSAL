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
        domain="[('state', '=', 'failed'), ('is_discarded', '=', False)]",
        tracking=True,
        help="Select the failed QC record that requires reworking. "
             "Discarded QCs (2+ failures) are excluded.")

    # ==========================================
    # REWORK LIMIT COUNTER (The Core Logic)
    # ==========================================
    def _get_failed_qc_count(self, so_id, product_id):
        """How many FAILED Semi-Finished QCs exist for this SO + product."""
        return self.env['semi.finished.qc'].search_count([
            ('sale_order_id', '=', so_id),
            ('product_id', '=', product_id),
            ('state', '=', 'failed'),
        ])

    @api.constrains('semi_finished_qc_id')
    def _check_rework_limit(self):
        """Server-side backstop: a twice-failed product can never be reworked."""
        for rec in self:
            if not rec.semi_finished_qc_id:
                continue
            qc = rec.semi_finished_qc_id
            count = self._get_failed_qc_count(qc.sale_order_id.id, qc.product_id.id)
            if count >= 2:
                raise UserError(_(
                    "Rework Blocked:\n"
                    "There are already %s failed QCs for %s (SO %s). "
                    "A batch that has failed twice is DISCARDED — "
                    "no further rework is allowed."
                ) % (count, qc.product_id.display_name, qc.sale_order_id.name))

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
    # NEW: REWORK VALUES VALIDATION
    # All manual fields must be filled and strictly > 0 before confirming
    # ==========================================
    def _validate_rework_values(self):
        self.ensure_one()
        missing = []
        checks = [
            ('rework_qty', 'Rework Quantity'),
            ('freshly_produced_qty', 'Freshly Produced Quantity'),
            ('actual_rework_pct', 'Actual Rework Added %'),
            ('quantity_added', 'Quantity Added'),
            ('total_batch_qty', 'Total Batch Quantity'),
        ]
        for fname, label in checks:
            value = getattr(self, fname) or 0.0
            if value <= 0:
                missing.append('• %s' % label)
        return missing

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
            qc = rec.semi_finished_qc_id
            if qc and self._get_failed_qc_count(qc.sale_order_id.id, qc.product_id.id) >= 2:
                raise UserError(_(
                    "Rework Blocked: %s (SO %s) already has 2 failed QCs — "
                    "the batch is DISCARDED.") % (
                                    qc.product_id.display_name, qc.sale_order_id.name))

            # HARD GATE: every manual value must be filled and > 0
            missing = rec._validate_rework_values()
            if missing:
                raise UserError(_(
                    "Rework Validation Error:\n"
                    "The following fields are mandatory and cannot remain zero "
                    "before confirming the rework:\n\n%s"
                ) % '\n'.join(missing))

            rec.write({'state': 'passed'})
            rec.message_post(body=_("<b>Rework Check Sheet Passed.</b>"))

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
            return action

    def action_view_failed_qc(self):
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
        self.message_post(body=_("<b>Post-Rework QC Created:</b> %s" % new_qc.name))
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
    # DISCARD FLAG (for views and domains)
    # ==========================================
    is_discarded = fields.Boolean(
        string='Is Discarded',
        compute='_compute_is_discarded',
        search='_search_is_discarded',
        help="True when this SO+Product has 2 or more failed QCs.")

    @api.depends('sale_order_id', 'product_id', 'state')
    def _compute_is_discarded(self):
        for rec in self:
            if rec.sale_order_id and rec.product_id:
                count = self.search_count([
                    ('sale_order_id', '=', rec.sale_order_id.id),
                    ('product_id', '=', rec.product_id.id),
                    ('state', '=', 'failed'),
                ])
                rec.is_discarded = count >= 2
            else:
                rec.is_discarded = False

    def _search_is_discarded(self, operator, value):
        """Makes the computed flag usable inside domains."""
        is_true = value in (True, 'true', 'True', 1)
        self.env.cr.execute("""
            SELECT sale_order_id, product_id 
            FROM semi_finished_qc 
            WHERE state = 'failed' AND sale_order_id IS NOT NULL AND product_id IS NOT NULL
            GROUP BY sale_order_id, product_id 
            HAVING COUNT(id) >= 2
        """)
        pairs = self.env.cr.fetchall()

        if not pairs:
            return [('id', '=', False)] if (operator == '=' and is_true) else [('id', '!=', False)]

        so_ids = [p[0] for p in pairs]
        prod_ids = [p[1] for p in pairs]

        candidates = self.search([
            ('sale_order_id', 'in', so_ids),
            ('product_id', 'in', prod_ids)
        ])

        valid_ids = [c.id for c in candidates if (c.sale_order_id.id, c.product_id.id) in pairs]

        if (operator == '=' and is_true) or (operator == '!=' and not is_true):
            return [('id', 'in', valid_ids)]
        else:
            return [('id', 'not in', valid_ids)]

    # ==========================================
    # DROPDOWN FILTER: Hide Failed/Discarded Products
    # ==========================================
    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        """Same rule as the base module: mixing done + not QC'd yet.
        Draft / In-Progress QCs must NOT hide the product."""
        for rec in self:
            if not rec.sale_order_id:
                rec.allowed_recipe_product_ids = False
                continue

            so_products = rec.sale_order_id.order_line.mapped('product_id')

            mixed_products = self.env['mixing.slip'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', '=', 'done'),
            ]).mapped('recipe_product_id')

            qcd_products = self.search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('state', 'in', ('passed', 'failed')),
            ]).mapped('product_id')

            rec.allowed_recipe_product_ids = so_products.filtered(
                lambda p: p in mixed_products and p not in qcd_products
            )

    def _is_product_qc_blocked(self, product):
        self.ensure_one()
        # 1. Discarded (2+ failed QCs)
        count = self.search_count([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('product_id', '=', product.id),
            ('state', '=', 'failed'),
        ])
        if count >= 2:
            return True

        qcs = self.search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('product_id', '=', product.id),
        ])
        if not qcs:
            return False

        # 2. Active QC exists (no parallel QCs)
        if qcs.filtered(lambda q: q.state in ('draft', 'in_progress') and q.id != self.id):
            return True

        # 3. Failed 1st QC waiting for rework
        failed_qcs = qcs.filtered(lambda q: q.state == 'failed' and q.id != self.id)
        if failed_qcs:
            return True

        return False

    # ==========================================
    # HARD BLOCK: Prevent manual creation for Discarded Batches
    # ==========================================
    @api.constrains('sale_order_id', 'product_id')
    def _check_discarded_batch_creation(self):
        for rec in self:
            if not (rec.sale_order_id and rec.product_id):
                continue
            # Allow the 2nd failed QC itself to be saved
            if rec.state == 'failed':
                continue

            count = self.search_count([
                ('id', '!=', rec.id),
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('product_id', '=', rec.product_id.id),
                ('state', '=', 'failed'),
            ])
            if count >= 2:
                raise UserError(_(
                    "QC Creation Blocked:\n"
                    "%s (SO %s) already has 2 failed QCs — it is DISCARDED; "
                    "a new QC cannot be created for it."
                ) % (rec.product_id.display_name, rec.sale_order_id.name))

    def action_fail(self):
        res = super().action_fail()
        for rec in self:
            count = self.search_count([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('product_id', '=', rec.product_id.id),
                ('state', '=', 'failed'),
            ])
            if count >= 2:
                # Second failure -> DISCARD
                rec.message_post(body=_(
                    "<b>BATCH DISCARDED.</b><br/>"
                    "This is the 2nd failed QC for %s (SO %s). The batch will "
                    "NOT be sent for a second rework — it must be discarded."
                ) % (rec.product_id.display_name, rec.sale_order_id.name))
            else:
                # First failure -> auto-create Rework Sheet and REDIRECT
                sheet = self.env['kalsal.rework.sheet'].search([
                    ('semi_finished_qc_id', '=', rec.id)], limit=1)
                if not sheet:
                    sheet = self.env['kalsal.rework.sheet'].create({
                        'semi_finished_qc_id': rec.id})
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