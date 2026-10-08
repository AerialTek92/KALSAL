from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        pickings_before = self
        res = super().button_validate()

        done_pickings = pickings_before.filtered(lambda p: p.state == 'done')
        for pick in done_pickings:
            mrs = self.env['material.requisition.slip'].search([('picking_id', '=', pick.id)], limit=1)
            if mrs:
                mrs._notify_production_users_mixing_required()

        return res