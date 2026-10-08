from odoo import models, _
from odoo.exceptions import UserError
from odoo.exceptions import AccessError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        """
        Security Override: Restrict receiving incoming goods to Quality department only.
        """
        for picking in self:
            if picking.picking_type_id.code == 'incoming':
                if not self.env.user.has_group('fk_kalsal_security.group_executor_stores'):
                    raise UserError(_(
                        "Access Denied: Only the Store department can receive goods.\n\n"
                        "Please wait for the Inventory team to Validate the Stock Picking."
                    ))
        return super(StockPicking, self).button_validate()

    def action_print_grn(self):
        """Block non-Store users from printing the GRN via direct API/URL calls."""
        if not self.env.user.has_group('fk_kalsal_security.group_executor_stores'):
            raise AccessError(_("Only Store/Inventory users (Executor: Stores) are authorized to print the GRN."))
        return super().action_print_grn()
