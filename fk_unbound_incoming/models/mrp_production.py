from odoo import models, fields, api


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def _compute_shortness_summary(self):
        """Enhance the shortness summary to mention unbound incoming stock."""
        for rec in self:
            short_items = rec.move_raw_ids.filtered(lambda l: l.x_items_short > 0)

            if short_items:
                display_list = []
                for move in short_items[:2]:
                    # Get unbound incoming for this product
                    unbound = move.product_id.unbound_incoming_desc or ''
                    if unbound:
                        display_list.append(
                            f"{move.product_id.name} ({move.x_items_short}{move.product_uom.name}, +{unbound})")
                    else:
                        display_list.append(
                            f"{move.product_id.name} ({move.x_items_short}{move.product_uom.name})")

                if len(short_items) > 2:
                    remaining = len(short_items) - 2
                    rec.x_shortness_summary = f"{', '.join(display_list)} ... (+{remaining} more)"
                else:
                    rec.x_shortness_summary = ", ".join(display_list)
            else:
                rec.x_shortness_summary = "✓ All Stock Available"