from odoo import models, fields, api, _
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    vehicle_number = fields.Char(
        string='Vehicle Number', tracking=True, copy=False,
        help="Vehicle number for this delivery. Printed on the Delivery Challan.")

    sto_number = fields.Char(
        string='STO No', tracking=True, copy=False,
        help="STO reference for this delivery. Printed on the Delivery Challan.")

    def button_validate(self):
        """Mandatory gate: an outgoing delivery cannot be validated without
        Vehicle Number and STO No."""
        for pick in self:
            if pick.picking_type_id.code == 'outgoing':
                missing = [label for label, value in (
                    ('Vehicle Number', pick.vehicle_number),
                    ('STO No', pick.sto_number),
                ) if not value]
                if missing:
                    raise UserError(_(
                        "Delivery %s cannot be validated.\n\n"
                        "The following mandatory field(s) are empty:\n• %s")
                        % (pick.name, '\n• '.join(missing)))
        return super().button_validate()