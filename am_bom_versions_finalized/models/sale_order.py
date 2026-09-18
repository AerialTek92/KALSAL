from odoo import api, fields, models, _
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    fg_mo_count = fields.Integer(
        string='FG MO Count',
        compute='_compute_fg_mo_count'
    )


    def _compute_fg_mo_count(self):
        for order in self:
            # Count how many fg_mo_id records exist across all lines in this order
            order.fg_mo_count = len(order.order_line.mapped('fg_mo_id'))

    def action_view_fg_mos(self):
        """ Opens the FG Manufacturing Orders linked to this Sales Order """
        self.ensure_one()
        fg_mos = self.order_line.mapped('fg_mo_id')

        # Fetch the standard MRP action
        action = self.env["ir.actions.actions"]._for_xml_id("mrp.mrp_production_action")
        action['domain'] = [('id', 'in', fg_mos.ids)]
        action['context'] = {'default_origin': self.name}

        # If there is only 1 MO, open it directly in form view
        if len(fg_mos) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = fg_mos.id
            action['views'] = [(False, 'form')]

        return action

    def action_confirm(self):
        # 1. Run standard confirmation (This triggers MTO & generates the Semi-Finished MO)
        res = super(SaleOrder, self).action_confirm()

        # 2. Automatically generate the FG MO and link it
        for order in self:
            for line in order.order_line:
                # If this line has an FG configured and hasn't had an MO created yet
                if line.finished_product_id and line.fg_bom_id and not line.fg_mo_id:

                    if line.finished_product_qty <= 0:
                        raise UserError(_("Finished Good Quantity must be greater than zero to confirm the order."))

                    # Create the Finished Good MO
                    mo_vals = {
                        'product_id': line.finished_product_id.id,
                        'product_qty': line.finished_product_qty,
                        'product_uom_id': line.finished_product_id.uom_id.id,
                        'bom_id': line.fg_bom_id.id,
                        'origin': f"{order.name} (Line: {line.name or 'New'})",
                        'company_id': line.company_id.id,
                        'custom_sale_line_id': line.id,  # Tag this one too
                    }
                    fg_mo = self.env['mrp.production'].create(mo_vals)
                    line.fg_mo_id = fg_mo.id

                    # Find the Semi-Finished MO that Odoo's native MTO just created a split-second ago
                    sf_mo = self.env['mrp.production'].search([
                        ('custom_sale_line_id', '=', line.id),
                        ('id', '!=', fg_mo.id)
                    ], limit=1)

                    # Establish the two-way link between the FG MO and the SF MO
                    if sf_mo:
                        fg_mo.linked_mo_id = sf_mo.id
                        sf_mo.linked_mo_id = fg_mo.id

        return res

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    product_uom_qty = fields.Float(digits=(16, 4))

    finished_product_id = fields.Many2one(
        'product.product',
        string='Finished Good',
        domain="[('bom_ids', '!=', False)]"
    )

    # Helper field to filter the BOM dropdown correctly
    finished_product_tmpl_id = fields.Many2one(
        'product.template',
        related='finished_product_id.product_tmpl_id'
    )

    # NEW: Must explicitly select the approved Finished Good BOM
    fg_bom_id = fields.Many2one(
        'mrp.bom',
        string='FG Recipe Version',
        domain="[('product_tmpl_id', '=', finished_product_tmpl_id), ('state', '=', 'approved')]",
        help="Select the approved BOM for the Finished Good."
    )


    # Default changed to 0.0
    carton_qty = fields.Float(
        string='Cartons Ordered',
        default=0.0,
        help="Type Cartons to auto-calculate FG Units. Leave at 0 to enter FG Units manually."
    )

    # Default changed to 0.0
    finished_product_qty = fields.Float(
        string='Total FG Units',
        digits='Product Unit of Measure',
        default=0.0
    )

    fg_mo_id = fields.Many2one(
        'mrp.production',
        string='FG MO',
        readonly=True,
        copy=False,
        help="The Manufacturing Order for this line's Finished Good."
    )

    @api.onchange('carton_qty', 'finished_product_id')
    def _onchange_carton_qty(self):
        """
        If Cartons are > 0, calculate FG Qty automatically.
        If Cartons are 0, clear FG Qty so the user can type it manually.
        """
        if self.carton_qty > 0 and self.finished_product_id and self.finished_product_id.product_tmpl_id.custom_sku:
            self.finished_product_qty = self.carton_qty * self.finished_product_id.product_tmpl_id.custom_sku
        elif self.carton_qty == 0:
            self.finished_product_qty = 0.0
    @api.onchange('finished_product_id')
    def _onchange_finished_product_id_clear(self):
        """When the Finished Good is changed, clear out the dependent fields."""
        self.fg_bom_id = False
        self.product_id = False
        self.finished_product_qty = 1.0

    @api.onchange('fg_bom_id')
    def _onchange_fg_bom_id_find_component(self):
        """Once the explicit FG BOM is selected, find its Semi-Finished component."""
        if not self.fg_bom_id:
            return {'domain': {'product_id': []}}

        # Find the semi-finished lines directly inside the explicitly chosen BOM
        semi_finished_lines = self.fg_bom_id.bom_line_ids.filtered(
            # NOTE: Verify if product_type_custom is on template or variant!
            lambda bl: bl.product_id.product_tmpl_id.product_type_custom == 'semi-finished'
        )

        unique_sf_products = semi_finished_lines.mapped('product_id')

        if not unique_sf_products:
            self.fg_bom_id = False
            return {'warning': {'title': _('No Component'),
                                'message': _("This Recipe Version has no Semi-Finished component.")}}

        if len(unique_sf_products) == 1:
            self.product_id = unique_sf_products[0]
            # Since self.product_id changes here, it will automatically trigger your
            # existing `_onchange_product_id_set_default_bom` method to fill out `bom_id`!
        else:
            self.product_id = False
            return {
                'domain': {'product_id': [('id', 'in', unique_sf_products.ids)]},
                'warning': {'title': _('Multiple Options'),
                            'message': _("Please select a specific component from the Product dropdown.")}
            }

    @api.onchange('finished_product_qty', 'fg_bom_id', 'product_id')
    def _onchange_calculate_component_qty(self):
        """Calculate standard SO Line quantity based on the selected FG BOM ratio."""
        if not (self.fg_bom_id and self.finished_product_qty and self.product_id):
            return

        matching_bom_line = self.fg_bom_id.bom_line_ids.filtered(
            lambda bl: bl.product_id.id == self.product_id.id
        )

        if not matching_bom_line:
            return

        bom_line = matching_bom_line[0]
        bom = self.fg_bom_id

        factor = self.finished_product_qty / bom.product_qty if bom.product_qty else 0.0
        self.product_uom_qty = factor * bom_line.product_qty