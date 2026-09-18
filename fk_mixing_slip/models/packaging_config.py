from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


# ==========================================
# PRODUCT: NEW CATEGORY TYPES + CARTON MASTER DATA
# ==========================================
class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Extends the Category Type defined in am_so_to_mrp

    pcs_per_carton = fields.Float(
        string='Pcs per Carton', default=1.0,
        help="Saleable pieces (boxes) inside one carton, e.g. 12 x 12 = 144.")

    semi_product_id = fields.Many2one(
        'product.product', string='Semi-Finished (Bulk) Product',
        help="On a finished (boxed) product: the bulk product whose Kit BOM "
             "dissolves into this product's packaging BOM. SFG QC tests the bulk.")
    RECIPE_CATEGORIES = [
        ('recipe_mixes', 'Recipe Mixes'),
        ('plain_spices', 'Plain Spices'),
        ('powdered_desserts', 'Powdered Desserts'),
    ]

    recipe_category = fields.Selection(
        selection=RECIPE_CATEGORIES,
        string='Recipe Category',
        help="Recipe family this product belongs to. "
             "Displayed read-only on the Semi-Finished QC.")

    @api.onchange('product_type_custom')
    def _onchange_product_type_custom_extended(self):
        """Routes/flags for the two NEW types (finished/raw stay in am_so_to_mrp)."""
        # Odoo 18+ renamed 'stock.location.route' to 'stock.route'
        RouteModel = 'stock.route' if 'stock.route' in self.env else 'stock.location.route'

        mto_route = self.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
        if not mto_route:
            mto_route = self.env.ref('stock.route_mto', raise_if_not_found=False)
        if not mto_route:
            mto_route = self.env[RouteModel].search(
                [('name', 'ilike', 'Replenish on Order')], limit=1)

        buy_route = self.env.ref('purchase.route_warehouse0_buy', raise_if_not_found=False)
        if not buy_route:
            buy_route = self.env.ref('purchase.route_buy', raise_if_not_found=False)
        if not buy_route:
            buy_route = self.env[RouteModel].search(
                [('name', 'ilike', 'Buy')], limit=1)

        raw_cat = self.env['product.category'].search(
            [('name', '=', 'Raw Material')], limit=1)

        for rec in self:
            if rec.product_type_custom == 'semi':
                # Bulk: never purchased, no routes at all (Kit BOM container only)
                rec.purchase_ok = False
                for route in (mto_route, buy_route):
                    if route and route.id in rec.route_ids.ids:
                        rec.route_ids = [(3, route.id)]
            elif rec.product_type_custom == 'packaging':
                # Packaging: purchased & stocked exactly like a raw material
                rec.purchase_ok = True
                if mto_route and mto_route.id in rec.route_ids.ids:
                    rec.route_ids = [(3, mto_route.id)]
                if buy_route and buy_route.id not in rec.route_ids.ids:
                    rec.route_ids = [(4, buy_route.id)]
                if raw_cat:
                    rec.categ_id = raw_cat.id

    @api.onchange('pcs_per_carton')
    def _onchange_pcs_per_carton_bom_warning(self):
        for rec in self:
            if not rec.pcs_per_carton:
                continue
            bom = self.env['mrp.bom'].search([
                ('product_tmpl_id', '=', rec.id), ('type', '=', 'normal'),
            ], limit=1)
            if bom and bom.product_qty != rec.pcs_per_carton:
                return {'warning': {
                    'title': _('BOM Quantity Mismatch'),
                    'message': _(
                        'The packaging BOM of this product has quantity %s but '
                        'Pcs per Carton is now %s. The BOM must be defined per '
                        '1 carton - please update its quantity.')
                               % (bom.product_qty, rec.pcs_per_carton),
                }}


class ProductProduct(models.Model):
    _inherit = 'product.product'

    pcs_per_carton = fields.Float(
        related='product_tmpl_id.pcs_per_carton', store=True,
        string='Pcs per Carton')


# ==========================================
# SALE ORDER LINE: TWO-WAY CARTONS ⇄ PCS SYNC
# ==========================================
class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    carton_qty = fields.Float(
        string='Cartons',
        help="Type cartons here OR type pieces in Quantity - the other column follows.")

    allowed_semi_bom_ids = fields.Many2many(
        'mrp.bom', string='Allowed Semi Recipe Versions',
        compute='_compute_allowed_semi_bom_ids')

    semi_bom_id = fields.Many2one(
        'mrp.bom', string='Semi-Finished Recipe Version',
        domain="[('id', 'in', allowed_semi_bom_ids)]",
        help="Which approved Kit (semi-finished) recipe version this order "
             "line must explode. Defaults to the latest approved version.")

    @api.depends('product_id')
    def _compute_allowed_semi_bom_ids(self):
        for line in self:
            semi = line.product_id.product_tmpl_id.semi_product_id
            if not semi:
                line.allowed_semi_bom_ids = False
                continue
            order = 'version desc, id desc' if 'version' in self.env['mrp.bom']._fields else 'id desc'
            line.allowed_semi_bom_ids = self.env['mrp.bom'].search([
                ('type', '=', 'phantom'),
                ('state', '=', 'approved'),
                ('active', '=', True),
                ('product_tmpl_id', '=', semi.product_tmpl_id.id),
            ], order=order)

    @api.onchange('product_id')
    def _onchange_product_default_semi_bom(self):
        for line in self:
            if line.product_id:
                line.semi_bom_id = line.allowed_semi_bom_ids[:1]

    @api.onchange('carton_qty')
    def _onchange_carton_qty(self):
        """Cartons typed -> Quantity (pcs) = cartons x pcs_per_carton."""
        for line in self:
            if line.carton_qty:
                ppc = line.product_id.pcs_per_carton or 1.0
                line.product_uom_qty = line.carton_qty * ppc

    @api.onchange('product_uom_qty')
    def _onchange_qty_sync_cartons(self):
        """Pieces typed -> Cartons = qty / pcs_per_carton."""
        for line in self:
            ppc = line.product_id.pcs_per_carton or 1.0
            line.carton_qty = (line.product_uom_qty or 0.0) / ppc

    @api.onchange('product_id')
    def _onchange_product_sync_cartons(self):
        """Product changed (ratio changed) -> refresh cartons from qty."""
        for line in self:
            ppc = line.product_id.pcs_per_carton or 1.0
            line.carton_qty = (line.product_uom_qty or 0.0) / ppc


# ==========================================
# BOM GUARD: PACKAGING BOM MUST BE PER 1 CARTON
# ==========================================
class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    @api.model
    def _bom_find(self, products, picking_type=None, company_id=False, bom_type=False, **kwargs):
        res = super()._bom_find(products, picking_type=picking_type,
                                company_id=company_id, bom_type=bom_type, **kwargs)
        if not isinstance(res, dict):
            return res
        override = self.env.context.get('kit_bom_override') or {}
        for product in products:
            forced = override.get(product.product_tmpl_id.id) or override.get(product.id)
            bom = res.get(product)
            if forced:
                res[product] = self.browse(forced)
            elif bom and bom.type == 'phantom' and 'state' in self._fields \
                    and bom.state != 'approved':
                res[product] = self.search([
                    ('type', '=', 'phantom'), ('active', '=', True),
                    ('state', '=', 'approved'),
                    ('company_id', 'in', [self.env.company.id, False]),
                    '|', ('product_id', '=', product.id),
                    ('product_tmpl_id', '=', product.product_tmpl_id.id),
                ], limit=1)
        return res

    @api.constrains('product_qty', 'product_tmpl_id', 'type')
    def _check_packaging_bom_per_carton(self):
        for bom in self:
            tmpl = bom.product_tmpl_id
            if (bom.type == 'normal'
                    and tmpl.product_type_custom == 'finished'
                    and tmpl.pcs_per_carton):
                if bom.product_qty != tmpl.pcs_per_carton:
                    raise ValidationError(_(
                        "The BOM of %s must be defined per 1 carton: its quantity "
                        "must equal the product's Pcs per Carton (%s). "
                        "Current BOM quantity: %s.")
                                          % (tmpl.display_name, tmpl.pcs_per_carton, bom.product_qty))


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    def action_confirm(self):
        """Explode with the SO line's chosen Kit version, then source raw
        materials from WH/Production (MRS delivers them there)."""
        kit_map = {}
        for mo in self:
            if not mo.origin:
                continue
            so = self.env['sale.order'].search([('name', '=', mo.origin)], limit=1)
            so_line = so.order_line.filtered(
                lambda l: l.product_id == mo.product_id)[:1]
            if so_line and so_line.semi_bom_id:
                kit_map[so_line.semi_bom_id.product_tmpl_id.id] = so_line.semi_bom_id.id
        if kit_map:
            self = self.with_context(kit_bom_override=kit_map)
        res = super().action_confirm()
        for mo in self:
            if mo.state in ('done', 'cancel'):
                continue
            if kit_map:
                mo._compute_move_raw_ids()   # re-explode with the chosen Kit
            wh = self.env['stock.warehouse'].search(
                [('company_id', '=', mo.company_id.id)], limit=1)
            prod_loc = self.env['stock.location'].search([
                ('usage', '=', 'production'),
                ('id', 'child_of', wh.view_location_id.id),
            ], limit=1)
            if not prod_loc:
                continue
            # mo.do_unreserve()
            mo.move_raw_ids.filtered(
                lambda m: m.state not in ('done', 'cancel')
            ).write({'location_id': prod_loc.id})
        return res


