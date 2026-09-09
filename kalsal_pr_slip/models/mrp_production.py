from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    # ... existing fields ...
    mrs_ids = fields.One2many(
        'material.requisition.slip', 'mrp_production_id',
        string='Material Requisition Slips')

    # ... existing methods unchanged ...

    def write(self, vals):
        if any(f in vals for f in ['move_raw_ids', 'product_qty', 'qty_producing']):
            products = self.move_raw_ids.mapped('product_id')
            if products:
                self.env.cr.execute(
                    "SELECT id FROM product_product WHERE id IN %s FOR UPDATE",
                    (tuple(products.ids),)
                )
        res = super(MrpProduction, self).write(vals)
        if any(f in vals for f in ['move_raw_ids', 'product_qty', 'qty_producing']):
            self._generate_forecast_budget()

        # NEW: MO -> MRS sync
        if 'qty_producing' in vals and not self.env.context.get('skip_mrs_sync'):
            for mo in self:
                open_mrs = mo.mrs_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
                for mrs in open_mrs:
                    if float_compare(mrs.qty_producing, vals['qty_producing'], precision_digits=2) != 0:
                        mrs.with_context(skip_mo_sync=True).write({'qty_producing': vals['qty_producing']})
        return res