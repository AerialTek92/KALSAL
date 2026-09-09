from datetime import datetime
from odoo import models, fields, api

class KalsalQualityParameter(models.Model):
    _name = 'kalsal.quality.parameter'
    _description = 'Global Quality Parameter'
    _order = ' sequence, id'

    sequence = fields.Integer(string='Sequence', default=10)
    name = fields.Char(string='Parameter Name', required=True, translate=True)
    default_condition = fields.Selection([
        ('nmt', 'Not More Than'),
        ('nlt', 'Not Less Than'),
    ])
    default_specification = fields.Char(string='Default Specification')
    active = fields.Boolean(string='Active', default=True)

from odoo import models, fields, api


class ColorParameter(models.Model):
    _name = 'color.parameter'
    _description = 'Product Color Parameter'

    name = fields.Char(string='Color Name', required=True)

    # FIX 1: Changed to point to product.template and renamed to product_tmpl_id
    product_tmpl_id = fields.Many2one('product.template', string='Product Template', ondelete='cascade')

    @api.model_create_multi
    def create(self, vals_list):
        # Capitalize the name before saving
        for vals in vals_list:
            if vals.get('name'):
                vals['name'] = vals['name'].strip().title()
        return super().create(vals_list)

    def write(self, vals):
        # Capitalize the name when updating
        if vals.get('name'):
            vals['name'] = vals['name'].strip().title()
        return super().write(vals)
