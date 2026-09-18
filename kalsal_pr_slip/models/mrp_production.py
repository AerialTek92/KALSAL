from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round


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


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    # Label-only rename: technical value 'phantom' stays identical,
    # so ALL existing logic (explosion, approvals, domains) is unaffected.
    type = fields.Selection(
        selection=[
            ('normal', 'FG Packaging'),
            ('phantom', 'Recipe'),
        ],
        string='BoM Type',
    )

    @api.model
    def _get_kg_uom(self):
        """Reference 'kg' UoM via the standard xmlid — not by name, since
        UoM display names can be translated/renamed."""
        return self.env.ref('uom.product_uom_kgm', raise_if_not_found=False)


    def _get_bom_product_tmpl(self):
        self.ensure_one()
        return self.product_tmpl_id or (
            self.product_id.product_tmpl_id if self.product_id else self.env['product.template'])


    # ---------- Default new semi-finished BOMs to "per 1 kg" ----------
    @api.onchange('product_tmpl_id', 'product_id')
    def _onchange_product_semi_finished_uom(self):
        for bom in self:
            tmpl = bom._get_bom_product_tmpl()
            if tmpl and tmpl.product_type_custom == 'semi':
                kg = bom._get_kg_uom()
                bom.type = 'phantom'
                if kg and bom.product_uom_id != kg:
                    bom.product_uom_id = kg.id
                if not bom.product_qty:
                    bom.product_qty = 1.0

    # ---------- Hard validation at save time ----------
    def _check_semi_kg_balance(self):
        """Unique name on purpose — never shadowed by other mrp.bom inheriters."""
        kg = self._get_kg_uom()
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')

        for bom in self:
            tmpl = bom._get_bom_product_tmpl()
            if not tmpl or tmpl.product_type_custom != 'semi':
                continue

            # (a) BOM output UoM must be kg
            if not kg or bom.product_uom_id != kg:
                raise ValidationError(_(
                    "BOM for '%(product)s' is Semi Finished (Bulk) and must "
                    "be defined in kg — its current unit is '%(uom)s'."
                ) % {
                                          'product': tmpl.display_name,
                                          'uom': bom.product_uom_id.display_name,
                                      })

            if not bom.bom_line_ids:
                continue

            # (b) components must sum to the output qty, converted to kg
            total_kg = 0.0
            for line in bom.bom_line_ids:
                if not line.product_uom_id:
                    continue
                total_kg += line.product_uom_id._compute_quantity(
                    line.product_qty, kg, round=False)

            total_kg = float_round(total_kg, precision_digits=precision)
            expected_kg = float_round(bom.product_qty, precision_digits=precision)

            if float_compare(total_kg, expected_kg, precision_rounding=kg.rounding) != 0:
                raise ValidationError(_(
                    "BOM for '%(product)s' (Semi Finished) is unbalanced: "
                    "its components add up to %(total)s kg, but the BOM is set "
                    "to produce %(expected)s kg. Components may be entered in g "
                    "or kg, but their total must match the batch quantity exactly."
                ) % {
                                          'product': tmpl.display_name,
                                          'total': total_kg,
                                          'expected': expected_kg,
                                      })


    # Keep the constraint for header-side writes only — dotted paths removed,
    # the line side is now enforced by the overrides below.
    @api.constrains('product_qty', 'product_uom_id', 'product_tmpl_id', 'product_id', 'type')
    def _check_semi_finished_kg_composition(self):
        self._check_semi_kg_balance()


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

            if template.product_type_custom == 'finished':
                # 1. Set the Quantity based on the SKU
                if template.pcs_per_carton:
                    bom.product_qty = template.pcs_per_carton

                # 2. Set the Unit field (UoM) to match the product's default unit
                if template.uom_id:
                    bom.product_uom_id = template.uom_id

    @api.model_create_multi
    def create(self, vals_list):
        boms = super(MrpBom, self.with_context(skip_semi_kg_check=True)).create(vals_list)
        boms._check_semi_kg_balance()  # once, on final state
        return boms

    def write(self, vals):
        res = super(MrpBom, self.with_context(skip_semi_kg_check=True)).write(vals)
        self._check_semi_kg_balance()  # once, AFTER all commands applied
        return res

    def unlink(self):
        # deleting an unbalanced BOM must stay possible — don't gate it
        return super(MrpBom, self.with_context(skip_semi_kg_check=True)).unlink()


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    def write(self, vals):
        old_boms = self.bom_id
        res = super().write(vals)
        if not self.env.context.get('skip_semi_kg_check'):
            # direct/programmatic line write (outside a BOM form save)
            (old_boms | self.bom_id)._check_semi_kg_balance()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('skip_semi_kg_check'):
            lines.bom_id._check_semi_kg_balance()
        return lines

    def unlink(self):
        boms = self.bom_id
        res = super().unlink()
        if not self.env.context.get('skip_semi_kg_check'):
            boms._check_semi_kg_balance()
        return res
