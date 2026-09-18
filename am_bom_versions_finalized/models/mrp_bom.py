from odoo import api, fields, models


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    # ... [keep your existing state, version, and action methods here] ...

    @api.onchange('product_tmpl_id', 'product_id')
    def _onchange_product_set_bom_qty_and_uom(self):
        """
        Automatically set the BOM quantity based on the custom_sku
        of the selected Finished Good, along with its Unit of Measure.
        """
        for bom in self:
            # Determine the correct product template
            template = bom.product_tmpl_id

            # If they picked a specific variant, use that variant's template instead
            if bom.product_id:
                template = bom.product_id.product_tmpl_id

            if template:
                # 1. Set the Quantity based on the SKU
                if template.custom_sku:
                    bom.product_qty = template.custom_sku

                # 2. Set the Unit field (UoM) to match the product's default unit
                if template.uom_id:
                    bom.product_uom_id = template.uom_id




# ==========================================
# 1. ADD FIELDS TO MANUFACTURING ORDER
# ==========================================
class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    # Hidden field used to securely link MOs back to their exact SO Line
    custom_sale_line_id = fields.Many2one('sale.order.line', string="SO Line Ref", copy=False)

    # Visible field linking the FG MO and SF MO together
    linked_mo_id = fields.Many2one(
        'mrp.production',
        string="Related MO",
        readonly=True,
        help="Links the Finished Good MO to the Semi-Finished MO and vice-versa."
    )

    custom_sale_order_id = fields.Many2one(
        'sale.order',
        related='custom_sale_line_id.order_id',
        string="Sales Order",
        store=False
    )

    # NEW: Action to open the Sales Order
    def action_open_sale_order(self):
        """ Opens the original Sales Order linked to this MO """
        self.ensure_one()
        if self.custom_sale_order_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'sale.order',
                'view_mode': 'form',
                'res_id': self.custom_sale_order_id.id,
                'target': 'current',
            }

    def action_open_linked_mo(self):
        """ Opens the linked MO (FG -> SF, or SF -> FG) """
        self.ensure_one()
        if self.linked_mo_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'mrp.production',
                'view_mode': 'form',
                'res_id': self.linked_mo_id.id,
                'target': 'current',
            }


# ==========================================
# 2. UPDATE YOUR EXISTING STOCK RULE
# ==========================================
class StockRule(models.Model):
    _inherit = 'stock.rule'

    def _prepare_mo_vals(self, *args, **kwargs):
        res = super(StockRule, self)._prepare_mo_vals(*args, **kwargs)

        values = kwargs.get('values')
        if values is None and len(args) > 7:
            values = args[7]

        if isinstance(values, dict) and values.get('sale_line_id'):
            sale_line = self.env['sale.order.line'].browse(values['sale_line_id'])
            if sale_line.bom_id:
                res['bom_id'] = sale_line.bom_id.id

            # NEW: Securely tag the Semi-Finished MO with the Sales Order Line ID
            res['custom_sale_line_id'] = sale_line.id

        return res


