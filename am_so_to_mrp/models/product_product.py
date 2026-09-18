from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProductProduct(models.Model):
    _inherit = "product.product"

    lot_prefix = fields.Char(
        string="Lot Prefix",
        help="Prefix used while automatically generating Lot Numbers.",
        compute="_compute_lot_prefix",
        store=True,
        readonly=False,
    )

    # Add the field to product.product as a related field
    product_type_custom = fields.Selection(
        related='product_tmpl_id.product_type_custom',
        store=False
    )

    @api.depends("name")
    def _compute_lot_prefix(self):
        for product in self:
            # If it already has a value, keep it (allows manual override)
            if product.lot_prefix:
                product.lot_prefix = product.lot_prefix
                continue

            name = (product.name or "").strip()
            if not name:
                product.lot_prefix = False
                continue

            words = name.split()
            if len(words) > 1:
                # Multiple words: First letter of each word
                product.lot_prefix = "".join(w[0] for w in words).upper()
            else:
                # Single word: First two letters
                product.lot_prefix = words[0][:2].upper()

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    product_type_custom = fields.Selection([
        ('finished', 'Finished Goods'),
        ('raw', 'Raw Materials'),
        ('semi', 'Semi Finished (Bulk)'),
        ('packaging', 'Packaging Material'),
    ], string='Category Type',required=True, tracking=True)

    # Odoo already knows 'lot' exists in the selection list.
    is_storable = fields.Boolean(default='True')
    tracking = fields.Selection(default='lot')

    @api.constrains('product_type_custom', 'uom_id')
    def _check_semi_finished_uom(self):
        kg = self.env.ref('uom.product_uom_kgm', raise_if_not_found=False)
        for tmpl in self:
            if tmpl.product_type_custom == 'semi' and kg and tmpl.uom_id != kg:
                raise ValidationError(_(
                    "'%(product)s' is a Semi Finished (Bulk) product and "
                    "must use 'kg' as its Unit of Measure — its current "
                    "unit is '%(uom)s'."
                ) % {
                    'product': tmpl.display_name,
                    'uom': tmpl.uom_id.display_name,
                })

    @api.onchange('product_type_custom')
    def _onchange_product_type_custom(self):
        # 1. Fetch the standard MTO route
        mto_route = self.env.ref(
            'stock.route_warehouse0_mto',
            raise_if_not_found=False
        )

        if not mto_route:
            mto_route = self.env['stock.location.route'].search(
                [('name', 'ilike', 'Replenish on Order')],
                limit=1
            )

        # 2. Fetch categories
        finished_cat = self.env['product.category'].search(
            [('name', '=', 'Finished Goods')],
            limit=1
        )

        raw_cat = self.env['product.category'].search(
            [('name', '=', 'Raw Material')],
            limit=1
        )

        for rec in self:

            # ==========================================================
            # FINISHED GOODS + SEMI FINISHED GOODS
            # ==========================================================
            if rec.product_type_custom in ('finished', 'semi'):

                # Purchase OFF
                rec.purchase_ok = False

                # MTO ON
                if mto_route and mto_route not in rec.route_ids:
                    rec.route_ids = [(4, mto_route.id)]

                # Finished Goods Category
                if finished_cat:
                    rec.categ_id = finished_cat.id

            # ==========================================================
            # RAW MATERIALS + PACKAGING MATERIALS
            # ==========================================================
            elif rec.product_type_custom in ('raw', 'packaging'):

                # Purchase ON
                rec.purchase_ok = True

                # MTO OFF
                if mto_route and mto_route in rec.route_ids:
                    rec.route_ids = [(3, mto_route.id)]

                # Raw Material Category
                if raw_cat:
                    rec.categ_id = raw_cat.id