from odoo import models, fields, api, _
from odoo.exceptions import UserError

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    lot_location_count = fields.Integer(
        string='Lot/Location Count',
        compute='_compute_lot_location_count',
    )

    def _compute_lot_location_count(self):
        for template in self:
            template.lot_location_count = self.env['stock.quant'].search_count([
                ('product_id', 'in', template.product_variant_ids.ids),
                ('quantity', '>', 0),
                ('lot_id', '!=', False),
            ])

    def action_view_lots_by_location(self):
        self.ensure_one()
        return {
            'name': _('Lots by Location'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.quant',
            'view_mode': 'kanban,list',
            'views': [
                (self.env.ref('kalsal_pr_slip.view_lot_by_location_kanban').id, 'kanban'),
                (self.env.ref('kalsal_pr_slip.view_lot_by_location_list').id, 'list'),
            ],
            'search_view_id': [self.env.ref('kalsal_pr_slip.view_lot_by_location_search').id, 'search'],
            'domain': [
                ('product_id', 'in', self.product_variant_ids.ids),
                ('quantity', '>', 0),
                ('lot_id', '!=', False),
                ('location_id.usage', '!=', 'inventory'),
            ],
            'context': {
                'search_default_groupby_location': 1,
                'group_by': ['location_id'],
            },
        }