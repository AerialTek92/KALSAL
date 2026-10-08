from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    is_finance_user = fields.Boolean(
        string='Is Finance User',
        compute='_compute_is_finance_user',
        help="Technical field used to restrict UI editing for dedicated Finance staff only."
    )

    def _is_finance_only_user(self):
        """True only for dedicated Finance staff — Factory Admin and CEO also
        inherit Finance rights but must retain full PO control, so they're excluded."""
        user = self.env.user
        return (
            user.has_group('fk_kalsal_security.group_management_finance')
            and not user.has_group('fk_kalsal_security.group_factory_admin')
        )

    @api.depends_context('uid')
    def _compute_is_finance_user(self):
        is_finance = self._is_finance_only_user()
        for rec in self:
            rec.is_finance_user = is_finance

    def button_confirm(self):
        if self._is_finance_only_user():
            raise UserError(_(
                "Access Denied: Finance users cannot confirm Purchase Orders.\n\n"
                "Please define the Payment Terms and notify the Procurement team to confirm the order."
            ))
        return super(PurchaseOrder, self).button_confirm()

    def action_view_picking(self):
        """
        Security Override: Prevent Procurement users from viewing incoming receipts.
        Only Quality department can access receiving documents.
        """
        # Check if any of the selected POs have incoming pickings
        for order in self:
            if order.picking_ids:
                incoming_pickings = order.picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming')
                if incoming_pickings:
                    # Check if user is Quality/Admin/CEO
                    if not self.env.user.has_group('fk_kalsal_security.group_management_quality'):
                        raise UserError(_(
                            "Access Denied: Only the Quality department can access Goods Receipt Notes.\n\n"
                            "Please wait for the Quality team to receive and inspect the goods."
                        ))
        return super(PurchaseOrder, self).action_view_picking()
