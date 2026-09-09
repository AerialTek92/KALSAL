from odoo import models, fields, api


class BudgetProductLine(models.Model):
    _inherit = 'budget.product.line'

    unbound_incoming_qty = fields.Float(
        string='Incoming (Free)',
        related='product_id.unbound_incoming_qty',
        help="Unbound incoming stock from pending POs")

    unbound_incoming_desc = fields.Char(
        string='Incoming POs',
        related='product_id.unbound_incoming_desc',
        help="List of unbound POs with quantities")

    net_exposure = fields.Float(
        string='Net Exposure',
        compute='_compute_net_exposure',
        help="Shortness minus unbound incoming (real purchasing need)")

    @api.depends('qty', 'unbound_incoming_qty')
    def _compute_net_exposure(self):
        for rec in self:
            rec.net_exposure = max(0.0, rec.qty - rec.unbound_incoming_qty)