from odoo import models, fields, api, _


class ProductProduct(models.Model):
    _inherit = 'product.product'

    unbound_incoming_qty = fields.Float(
        string='Unbound Incoming Qty',
        compute='_compute_unbound_incoming',
        help="Qty on pending POs that are NOT linked to any demand.")

    unbound_incoming_desc = fields.Char(
        string='Incoming (Unbound) POs',
        compute='_compute_unbound_incoming',
        help="e.g. PO001: 10 kg")

    unbound_incoming_move_ids = fields.Many2many(
        'stock.move', string='Unbound Incoming Moves',
        compute='_compute_unbound_incoming')


    def _get_unbound_incoming_moves(self):
        """Pending purchase moves that are not promised to any demand."""
        moves = self.env['stock.move'].search([
            ('product_id', 'in', self.ids),
            ('state', 'not in', ('done', 'cancel')),
            ('purchase_line_id', '!=', False),
            ('location_dest_id.usage', '=', 'internal'),
        ])
        return moves.filtered(
            lambda m: not m.move_dest_ids
                      and m.procure_method == 'make_to_stock')

    def _compute_unbound_incoming(self):
        grouped = {}
        for move in self._get_unbound_incoming_moves():
            grouped[move.product_id.id] = \
                grouped.get(move.product_id.id, self.env['stock.move']) | move

        for rec in self:
            moves = grouped.get(rec.id, self.env['stock.move'])

            # 1. Sum the total quantity
            rec.unbound_incoming_qty = sum(moves.mapped('product_uom_qty'))

            # 2. Group quantities by PO Name for a cleaner text description
            po_totals = {}
            for m in moves:
                po_name = m.purchase_line_id.order_id.name or 'Draft PO'
                uom_name = m.product_uom.name
                po_totals.setdefault((po_name, uom_name), 0.0)
                po_totals[(po_name, uom_name)] += m.product_uom_qty

            # Format: "PO001: 15.0 kg, PO002: 5.0 kg"
            rec.unbound_incoming_desc = ', '.join([
                '%s: %s %s' % (po_name, qty, uom_name)
                for (po_name, uom_name), qty in po_totals.items()
            ])

            rec.unbound_incoming_move_ids = moves
