from odoo import models, fields, api


class StockMove(models.Model):
    _inherit = 'stock.move'

    x_unbound_incoming_qty = fields.Float(
        string='Incoming (Free)',
        related='product_id.unbound_incoming_qty',
        help="Unbound incoming stock from pending POs")

    x_unbound_incoming_desc = fields.Char(
        string='Incoming POs',
        related='product_id.unbound_incoming_desc',
        help="List of unbound POs with quantities")