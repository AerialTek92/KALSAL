from odoo import models, _
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def action_manage_qc_parameters(self):
        """Open this product's QC parameter lines in a standalone editable
        window — works for users with READ-ONLY access on the product,
        because every write lands on the LINE model, never on the product."""
        self.ensure_one()
        model_map = {
            'raw': 'product.quality.parameter.line',
            'semi': 'semi.quality.parameter.line',
            'finished': 'finished.quality.parameter.line',
            'packaging': 'packaging.quality.parameter.line',
        }
        res_model = model_map.get(self.product_type_custom)
        if not res_model:
            raise UserError(_(
                "Set the Category Type (Raw / Semi / Finished / Packaging) on the "
                "General Information tab before managing QC parameters."))
        return {
            'name': _('QC Parameters — %s') % self.display_name,
            'type': 'ir.actions.act_window',
            'res_model': res_model,
            'view_mode': 'list',
            'domain': [('product_tmpl_id', '=', self.id)],
            'context': {'default_product_tmpl_id': self.id},
        }