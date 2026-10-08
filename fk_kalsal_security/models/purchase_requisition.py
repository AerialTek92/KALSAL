from odoo import models, _
from odoo.exceptions import UserError


class QuotationRequisitionLine(models.Model):
    _inherit = 'quotation.requisition.line'

    def _check_price_increase(self, price: float, remarks: str) -> None:
        """
        Override to strictly enforce that ONLY Factory Admin can approve price increases.
        Standard users are blocked even if they provide remarks.
        """
        highest_price: float = self._get_highest_recent_price()

        if highest_price and price > highest_price:
            if not self.env.user.has_group('fk_kalsal_security.group_factory_admin'):
                raise UserError(_(
                    "Price increase for %s is strictly prohibited for your user role.\n"
                    "Please contact the Factory Administrator to approve this quotation."
                ) % self.product_id.display_name)

        # If they are Factory Admin, or the price is not higher, proceed with standard checks
        super(QuotationRequisitionLine, self)._check_price_increase(price, remarks)