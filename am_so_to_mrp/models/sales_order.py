from odoo import models, fields, api,_

from odoo.exceptions import UserError


class SalesOrder(models.Model):
    _inherit = 'sale.order'

    x_budget_id = fields.Many2one('custom.budget', string='Master Forecast Budget')

    stock_ready = fields.Boolean(
        string='Stock Up',
        default=False,
        copy=False,
        store=True,  # <-- Make sure this is True
        help="True when all linked Purchase Orders for this SO are fully received in WH/Stock."
    )


    def action_custom_save(self):
        """
        Triggered by the custom Save button.
        Note: Odoo's UI automatically writes pending changes to the database
        before executing this method. We just return True to stay on the form.
        """
        return True

    def action_confirm(self):
        # 1. Custom logic BEFORE the sales order is confirmed
        # Example: if self.amount_total <= 0: raise UserError("Cannot confirm free order.")

        res = super(SalesOrder, self).action_confirm()

        # 2. Custom logic AFTER the sales order is confirmed
        for line in self.order_line:
            # Place your automated code here (e.g., logging, notifying, setting custom fields)
            if line.product_id and not line.product_id.bom_ids:
                raise UserError(
                    _('Bill Of Material for Product %s is not available. Manufacturing Order will not be created') % line.product_id.name)

        return res


class SalesOrderLine(models.Model):
    _inherit = 'sale.order.line'

    remark = fields.Char(string='Remark', store=True)

    # Keep store=False so numbers recalculate instantly when lines are deleted or moved
    s_no = fields.Integer(string='S No#', compute='_compute_s_no', store=False)

    product_id = fields.Many2one(
        domain="[('product_type_custom', '=', 'finished')]"
    )

    @api.depends('order_id.order_line')
    def _compute_s_no(self):
        for order in self.mapped('order_id'):
            # Loop through all lines of the order and assign 1-based index numbers
            for index, line in enumerate(order.order_line, start=1):
                line.s_no = index


