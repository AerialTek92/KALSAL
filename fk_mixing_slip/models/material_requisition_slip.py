from odoo import models, fields, api, _
from odoo.exceptions import UserError


class MaterialRequisitionSlip(models.Model):
    _inherit = 'material.requisition.slip'

    mixing_slip_ids = fields.One2many(
        'mixing.slip', 'mrs_id', string='Mixing Slips')

    mixing_slip_count = fields.Integer(
        string='Mixing Slip Count', compute='_compute_mixing_slip_count')

    picking_done = fields.Boolean(
        string='Picking Validated',
        compute='_compute_picking_done',
        store=False)

    @api.depends('mixing_slip_ids')
    def _compute_mixing_slip_count(self):
        for rec in self:
            rec.mixing_slip_count = len(rec.mixing_slip_ids)

    @api.depends('picking_id', 'picking_id.state')
    def _compute_picking_done(self):
        for rec in self:
            rec.picking_done = rec.picking_id and rec.picking_id.state == 'done'

    # ---------- NEW: mixing-gate helper ----------
    def _product_has_pending_mixing(self, product, sale_order, exclude_id=None):
        """True if a non-cancelled MRS already exists for this product/SO
        whose Mixing hasn't been completed yet — either no Mixing Slip
        was ever created for it, or one exists but isn't 'done'."""
        domain = [
            ('sale_order_id', '=', sale_order.id),
            ('recipe_product_id', '=', product.id),
            ('state', 'in', ('confirmed', 'done')),
        ]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        prior_mrs = self.env['material.requisition.slip'].search(domain)
        for mrs in prior_mrs:
            if not mrs.mixing_slip_ids or any(s.state != 'done' for s in mrs.mixing_slip_ids):
                return True
        return False

    # ---------- Hide products with pending mixing from the picker ----------
    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        super()._compute_allowed_recipe_product_ids()
        for rec in self:
            if not rec.sale_order_id or not rec.allowed_recipe_product_ids:
                continue
            blocked = self.env['product.product']
            for product in rec.allowed_recipe_product_ids:
                if rec._product_has_pending_mixing(product, rec.sale_order_id, exclude_id=rec.id):
                    blocked |= product
            rec.allowed_recipe_product_ids -= blocked

    # ---------- Onchange: block selection with a clear message ----------
    @api.onchange('recipe_product_id')
    def _onchange_recipe_product_id(self):
        res = super()._onchange_recipe_product_id()
        if self.recipe_product_id and self.sale_order_id:
            if self._product_has_pending_mixing(
                    self.recipe_product_id, self.sale_order_id, exclude_id=self.id):
                blocked_name = self.recipe_product_id.display_name
                self.recipe_product_id = False
                self.bom_id = False
                self.mrp_production_id = False
                self.recipe_line_ids = [(5, 0, 0)]
                return {
                    'warning': {
                        'title': _('Mixing Not Completed'),
                        'message': _(
                            "A previous batch for %s on this Sale Order "
                            "hasn't had its Mixing marked as Done yet. "
                            "Complete mixing for that batch before "
                            "requisitioning a new one."
                        ) % blocked_name,
                    }
                }
        return res

    # ---------- Hard safety net at confirm time ----------
    def action_confirm(self):
        for rec in self:
            if rec.sale_order_id and rec.recipe_product_id:
                if rec._product_has_pending_mixing(
                        rec.recipe_product_id, rec.sale_order_id, exclude_id=rec.id):
                    raise UserError(_(
                        "Cannot confirm: a previous batch for %s on %s still "
                        "has incomplete Mixing. Mark that batch's Mixing "
                        "Slip as Done first."
                    ) % (rec.recipe_product_id.display_name, rec.sale_order_id.name))
        return super().action_confirm()

    def action_open_mixing_slip(self):
        # unchanged from before
        self.ensure_one()
        slips = self.env['mixing.slip'].search(
            [('mrs_id', '=', self.id)], order='id desc')
        if not slips:
            slips = self.env['mixing.slip'].create({
                'sale_order_id': self.sale_order_id.id or False,
                'recipe_product_id': self.recipe_product_id.id or False,
                'mrs_id': self.id,
                'line_ids': [
                    (0, 0, {'mrs_line_id': line.id})
                    for line in self.recipe_line_ids
                ],
            })
        if len(slips) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Mixing Slip'),
                'res_model': 'mixing.slip',
                'res_id': slips.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Mixing Slips'),
            'res_model': 'mixing.slip',
            'view_mode': 'list,form',
            'domain': [('id', 'in', slips.ids)],
        }