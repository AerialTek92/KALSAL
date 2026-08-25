from odoo import models, fields, api


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    quality_param_line_ids = fields.One2many(
        'product.quality.parameter.line', 'product_tmpl_id',
        string='Quality Parameters')

    semi_fg_specs = fields.One2many(
        'semi.quality.parameter.line', 'product_tmpl_id',
        string='Quality Parameters')

    fg_specs = fields.One2many(
        'finished.quality.parameter.line', 'product_tmpl_id',
        string='Quality Parameters')

    # FIX 2: Changed the inverse field to match the new name 'product_tmpl_id'
    color_parameter_ids = fields.One2many('color.parameter', 'product_tmpl_id', string='Available Colors')




class RawQualityParameterLine(models.Model):
    _name = 'product.quality.parameter.line'
    _description = 'Raw Material Quality Parameter Configuration'
    _order = 'sequence, id'

    product_tmpl_id = fields.Many2one('product.template', string='Product', ondelete='cascade', required=True)
    parameter_id = fields.Many2one('kalsal.quality.parameter', string='Parameter', required=True, ondelete='restrict')
    sequence = fields.Integer(related='parameter_id.sequence', store=True)
    condition = fields.Selection([
        ('nmt', 'Not More Than'),
        ('nlt', 'Not Less Than'),
    ])
    delay_required = fields.Boolean(default=False, string='Delay Required')
    specification = fields.Char(string='Specification', required=True)

    @api.onchange('parameter_id')
    def _onchange_parameter_id(self):
        if self.parameter_id and not self.specification:
            self.specification = self.parameter_id.default_specification

        if self.parameter_id and not self.condition:
            self.condition = self.parameter_id.default_condition

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # If a dictionary is passed instead of an ID (e.g. from custom interface)
            # Or if you are intercepting a text name to create the global parameter dynamically:
            if 'parameter_id' in vals and isinstance(vals['parameter_id'], str):
                # Create the global parameter first
                global_param = self.env['kalsal.quality.parameter'].create({
                    'name': vals['parameter_id'],
                    'default_condition': vals.get('condition'),
                })
                # Reassign the actual ID back to the line values
                vals['parameter_id'] = global_param.id

        return super(RawQualityParameterLine, self).create(vals_list)

class SemiQualityParameterLine(models.Model):
    _name = 'semi.quality.parameter.line'
    _description = 'Semi Finished Quality Parameter Configuration'
    _order = 'sequence, id'

    product_tmpl_id = fields.Many2one('product.template', string='Product', ondelete='cascade', required=True)
    parameter_id = fields.Many2one('kalsal.quality.parameter', string='Parameter', required=True, ondelete='restrict')
    sequence = fields.Integer(related='parameter_id.sequence', store=True)
    condition = fields.Selection([
        ('nmt', 'Not More Than'),
        ('nlt', 'Not Less Than'),
    ])
    delay_required = fields.Boolean(default=False, string='Delay Required')
    specification = fields.Char(string='Specification', required=True)

    @api.onchange('parameter_id')
    def _onchange_parameter_id(self):
        if self.parameter_id and not self.specification:
            self.specification = self.parameter_id.default_specification

        if self.parameter_id and not self.condition:
            self.condition = self.parameter_id.default_condition

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # If a dictionary is passed instead of an ID (e.g. from custom interface)
            # Or if you are intercepting a text name to create the global parameter dynamically:
            if 'parameter_id' in vals and isinstance(vals['parameter_id'], str):
                # Create the global parameter first
                global_param = self.env['kalsal.quality.parameter'].create({
                    'name': vals['parameter_id'],
                    'default_condition': vals.get('condition'),
                })
                # Reassign the actual ID back to the line values
                vals['parameter_id'] = global_param.id

        return super(SemiQualityParameterLine, self).create(vals_list)

class FinishedQualityParameterLine(models.Model):
    _name = 'finished.quality.parameter.line'
    _description = 'Finished Quality Parameter Configuration'
    _order = 'sequence, id'

    product_tmpl_id = fields.Many2one('product.template', string='Product', ondelete='cascade', required=True)
    parameter_id = fields.Many2one('kalsal.quality.parameter', string='Parameter', required=True, ondelete='restrict')
    sequence = fields.Integer(related='parameter_id.sequence', store=True)
    condition = fields.Selection([
        ('nmt', 'Not More Than'),
        ('nlt', 'Not Less Than'),
    ])
    delay_required = fields.Boolean(default=False, string='Delay Required')
    specification = fields.Char(string='Specification', required=True)

    @api.onchange('parameter_id')
    def _onchange_parameter_id(self):
        if self.parameter_id and not self.specification:
            self.specification = self.parameter_id.default_specification

        if self.parameter_id and not self.condition:
            self.condition = self.parameter_id.default_condition

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # If a dictionary is passed instead of an ID (e.g. from custom interface)
            # Or if you are intercepting a text name to create the global parameter dynamically:
            if 'parameter_id' in vals and isinstance(vals['parameter_id'], str):
                # Create the global parameter first
                global_param = self.env['kalsal.quality.parameter'].create({
                    'name': vals['parameter_id'],
                    'default_condition': vals.get('condition'),
                })
                # Reassign the actual ID back to the line values
                vals['parameter_id'] = global_param.id

        return super(FinishedQualityParameterLine, self).create(vals_list)
