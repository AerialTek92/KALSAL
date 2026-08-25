from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class MaterialRequisitionSlip(models.Model):
    _name = 'material.requisition.slip'
    _description = 'Material Requisition Slip / Lot Making'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # ---------- Header ----------
    name = fields.Char(
        string='MRS No', required=True, copy=False, readonly=True,
        default=lambda self: _('New'), tracking=True)
    date = fields.Date(string='Date', default=fields.Date.context_today, tracking=True)

    sale_order_id = fields.Many2one(
        'sale.order', string='Sale Order', tracking=True,
        help="Select a Sale Order first. Its products will become available in 'Recipe Name'.")

    # New computed field to hold the allowed products
    allowed_recipe_product_ids = fields.Many2many(
        'product.product',
        string='Allowed Recipes',
        compute='_compute_allowed_recipe_product_ids'
    )

    recipe_product_id = fields.Many2one(
        'product.product', string='Recipe Name', tracking=True,
        help="Select a product (recipe) that exists in the chosen Sale Order.")

    kgs = fields.Float(string='Kgs', tracking=True)
    to_department_id = fields.Many2one('hr.department', string='To Department', tracking=True)

    uom_id = fields.Many2one('uom.uom', string='UOM', store=True, readonly=True)
    quantity = fields.Float(string='Quantity', store=True, tracking=True)

    bom_id = fields.Many2one('mrp.bom', string='Bill of Material', store=True, readonly=True)

    recipe_line_ids = fields.One2many(
        'material.requisition.line', 'slip_id', string='Recipe Lines', copy=True)

    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, required=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], default='draft', tracking=True, string='State')

    picking_id = fields.Many2one('stock.picking', string='Internal Transfer', readonly=True, copy=False)

    # ---------- Compute Allowed Products ----------
    # ---------- Compute Allowed Products ----------
    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        for rec in self:
            if rec.sale_order_id:
                # 1. Get all products from the Sale Order lines
                all_so_products = rec.sale_order_id.order_line.mapped('product_id')

                # 2. Find products that already have a 'done' MRS for this Sale Order
                domain = [
                    ('sale_order_id', '=', rec.sale_order_id.id),
                    ('state', '=', 'done'),
                ]
                # Exclude the current record if it's already saved (prevents hiding the product when editing an existing done MRS)
                if rec.id:
                    domain.append(('id', '!=', rec.id))

                done_mrs = self.env['material.requisition.slip'].search(domain)
                done_products = done_mrs.mapped('recipe_product_id')

                # 3. Only allow products that do NOT have a 'done' MRS
                rec.allowed_recipe_product_ids = all_so_products - done_products
            else:
                rec.allowed_recipe_product_ids = False

    # ---------- Sequence ----------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'material.requisition.slip') or _('New')
        return super().create(vals_list)

    # ---------- Onchange: SO -> clear downstream fields ----------
    @api.onchange('sale_order_id')
    def _onchange_sale_order_id(self):
        if self.sale_order_id:
            self.recipe_product_id = False
            self.uom_id = False
            self.quantity = 0.0
            self.bom_id = False
            self.recipe_line_ids = [(5, 0, 0)]

    # ---------- Onchange: Recipe product -> UOM, Qty, BOM lines ----------
    # ---------- Onchange: Recipe product -> UOM, Qty, BOM lines ----------
    @api.onchange('recipe_product_id')
    def _onchange_recipe_product_id(self):
        if not self.recipe_product_id:
            return
        if not self.sale_order_id:
            raise UserError(_('Please select a Sale Order first.'))

        so_line = self.sale_order_id.order_line.filtered(
            lambda l: l.product_id == self.recipe_product_id
        )
        if not so_line:
            raise UserError(_(
                'Selected recipe product is not present in the chosen Sale Order.'))

        line = so_line[0]
        self.uom_id = line.product_id.uom_id
        self.quantity = line.product_uom_qty
        self.kgs = line.product_uom_qty

        # Safe BOM search compatible with Odoo 19
        bom = self.env['mrp.bom'].search([
            ('product_id', '=', self.recipe_product_id.id),
            ('id', '=', line.bom_id.id)
        ], limit=1)
        if not bom:
            bom = self.env['mrp.bom'].search([
                ('product_tmpl_id', '=', self.recipe_product_id.product_tmpl_id.id),
                # ('product_id', '=', False),
                ('id', '=', line.bom_id.id)

            ], limit=1)

        if not bom:
            self.bom_id = False
            self.recipe_line_ids = [(5, 0, 0)]
            return {
                'warning': {
                    'title': _('No BOM'),
                    'message': _('No Bill of Material found for the selected recipe product.'),
                }
            }

        self.bom_id = bom
        self._populate_recipe_lines()

    @api.onchange('quantity')
    def _onchange_quantity(self):
        if self.bom_id:
            self._populate_recipe_lines()

    def _populate_recipe_lines(self):
        self.ensure_one()
        lines = [(5, 0, 0)]
        if not self.bom_id:
            self.recipe_line_ids = lines
            return

        bom = self.bom_id
        try:
            needed_qty_in_bom_uom = self.uom_id._compute_quantity(
                self.quantity or 1.0, bom.product_uom_id
            ) if self.uom_id else (self.quantity or 1.0)
        except Exception:
            needed_qty_in_bom_uom = self.quantity or 1.0

        if not bom.product_qty:
            factor = 1.0
        else:
            factor = needed_qty_in_bom_uom / bom.product_qty

        sno = 1
        for bl in bom.bom_line_ids:
            qty = bl.product_qty * factor
            lines.append((0, 0, {
                'sno': sno,
                'product_id': bl.product_id.id,
                'item_code': bl.product_id.default_code or '',
                'item_description': bl.product_id.name or '',
                'uom_id': bl.product_uom_id.id,
                'quantity_required': qty,  # Updated here
                'quantity_issued': 0.0,  # Added here
                'remarks': '',
            }))
            sno += 1
        self.recipe_line_ids = lines

    def action_confirm(self):
        Picking = self.env['stock.picking']
        StockLocation = self.env['stock.location']
        PickingType = self.env['stock.picking.type']
        Warehouse = self.env['stock.warehouse']

        for rec in self:
            if not rec.recipe_product_id:
                raise UserError(_('Please select a Recipe Name before confirming.'))
            if not rec.recipe_line_ids:
                raise UserError(_('No recipe lines to confirm.'))

            # 1. Find the Internal Operation Type for the company
            picking_type = PickingType.search([
                ('code', '=', 'internal'),
                ('company_id', '=', rec.company_id.id)
            ], limit=1)
            if not picking_type:
                raise UserError(_("No internal operation type found for this company!"))

            # 2. Find the Warehouse for this company
            warehouse = Warehouse.search([('company_id', '=', rec.company_id.id)], limit=1)
            if not warehouse:
                raise UserError(_("No warehouse configured for the current company."))

            # 3. Explicitly get WH/Stock (Source)
            source_location = warehouse.lot_stock_id

            # 4. Explicitly get WH/Production (Destination) by searching under the warehouse's view location
            dest_location = StockLocation.search([
                ('usage', '=', 'production'),
                ('id', 'child_of', warehouse.view_location_id.id)
            ], limit=1)

            # Fallback just in case
            if not dest_location:
                dest_location = StockLocation.search([
                    ('usage', '=', 'production'),
                    ('company_id', 'in', [rec.company_id.id, False])
                ], limit=1)

            if not source_location or not dest_location:
                raise UserError(
                    _("Source or Destination location is missing. Please configure your warehouse locations."))

            # 5. Prepare Stock Moves for each recipe line
            move_lines = []
            precision = self.env['decimal.precision'].precision_get(
                'Product Unit of Measure')  # Moved outside loop for speed

            for line in rec.recipe_line_ids:
                # float_compare returns 0 if the two values are equal
                is_different = float_compare(line.quantity_issued, line.quantity_required,
                                             precision_digits=precision) != 0

                if is_different and not line.remarks:
                    raise UserError(
                        _("Quantity issued for product %s is less than or equal to Required qty for making this product. Kindly add Remarks.") % line.product_id.name
                    )

                # Skip generation if issued quantity is 0 or negative
                if line.quantity_issued <= 0:
                    continue

                # Added mandatory 'name' field for the stock move line creation
                move_lines.append((0, 0, {
                    # 'name': line.product_id.display_name or line.product_id.name,
                    'product_id': line.product_id.id,
                    'product_uom': line.uom_id.id,
                    'product_uom_qty': line.quantity_issued,
                    'location_id': source_location.id,
                    'location_dest_id': dest_location.id,
                }))

            # 6. Create the Internal Transfer
            if move_lines:
                picking_vals = {
                    'partner_id': rec.sale_order_id.partner_id.id if rec.sale_order_id else False,
                    'picking_type_id': picking_type.id,
                    'origin': rec.name,
                    'location_id': source_location.id,
                    'location_dest_id': dest_location.id,
                    'move_ids': move_lines,
                }
                # Create the skeleton transfer record
                picking = Picking.create(picking_vals)

                # Let Odoo calculate routing, statuses, and standard behaviors
                picking.action_confirm()

                # FIX: Loop back through the newly created moves and force write the exact floats
                # to prevent Odoo's action_confirm() or reservation engine from wiping them out.
                for move in picking.move_ids:
                    # Find matching recipe line based on product
                    match_line = rec.recipe_line_ids.filtered(lambda l: l.product_id.id == move.product_id.id)
                    if match_line:
                        qty = match_line[0].quantity_issued
                        move.write({
                            'product_uom_qty': qty,
                            'quantity': qty  # Forces Odoo 19 'Counted/Done' quantity values
                        })

                rec.picking_id = picking.id

            rec.state = 'confirmed'

    def action_open_picking(self):
        self.ensure_one()
        if self.picking_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'stock.picking',
                'res_id': self.picking_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

    def action_done(self):
        for rec in self:
            rec.state = 'done'

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancel'

    def action_draft(self):
        for rec in self:
            rec.state = 'draft'


class MaterialRequisitionLine(models.Model):
    _name = 'material.requisition.line'
    _description = 'Material Requisition Line'

    slip_id = fields.Many2one(
        'material.requisition.slip', string='Slip',
        required=True, ondelete='cascade')
    sno = fields.Integer(string='S.No')
    product_id = fields.Many2one('product.product', string='Item')
    item_code = fields.Char(string='Item Code')
    item_description = fields.Char(string='Item Description')
    uom_id = fields.Many2one('uom.uom', string='UOM')
    quantity_required = fields.Float(string='Required', default=0.0)
    quantity_issued = fields.Float(string='Issued', default=0.0)
    remarks = fields.Char(string='Remarks')

    # @api.constrains('quantity_issued', 'quantity_required', 'remarks')
    # def _check_remarks_on_variance(self):
    #     for line in self:
    #         # If issued quantity is different from required quantity, remarks are mandatory
    #         if line.quantity_issued != line.quantity_required and not line.remarks:
    #             raise UserError(_(
    #                 "Remarks are required for product '%s' because the Issued quantity differs from the Required quantity."
    #             ) % line.product_id.name)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    stock_ready = fields.Boolean(
        string='Stock Up',
        default=False,
        copy=False,
        store=True,
        help="True when all linked Purchase Orders for this SO are fully received in WH/Stock."
    )


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        # 1. Standard Odoo validation
        res = super().button_validate()

        # If super returns an action (e.g., immediate transfer or backorder wizard),
        # we don't process our custom logic yet. The picking isn't fully validated.
        if res is not True:
            return res

        MRS = self.env['material.requisition.slip']
        MO = self.env['mrp.production']
        StockLocation = self.env['stock.location']
        Warehouse = self.env['stock.warehouse']

        for picking in self:
            # 2. Find the Warehouse and locations for this company
            warehouse = Warehouse.search([('company_id', '=', picking.company_id.id)], limit=1)
            if not warehouse:
                continue

            source_location = warehouse.lot_stock_id
            dest_location = StockLocation.search([
                ('usage', '=', 'production'),
                ('id', 'child_of', warehouse.view_location_id.id)
            ], limit=1)

            # 3. Check if this is the specific WH/Stock -> WH/Production transfer
            if not (source_location and dest_location and
                    picking.location_id == source_location and
                    picking.location_dest_id == dest_location):
                continue

            # 4. Find the linked Material Requisition Slip
            mrs = MRS.search([('name', '=', picking.origin)], limit=1)
            if not mrs:
                continue

            # 5. Find the exact Manufacturing Order using SO name, Product, and BOM
            mo = MO.search([
                ('product_id', '=', mrs.recipe_product_id.id),
                ('bom_id', '=', mrs.bom_id.id),
                ('origin', '=', mrs.sale_order_id.name),
                ('state', 'in', ['confirmed', 'progress', 'to_close'])  # States where reservation is possible
            ], limit=1)

            if not mo:
                continue

            # 6. Collect validated move lines from the picking that have lots
            picking_move_lines = picking.move_ids.move_line_ids.filtered(
                lambda ml: ml.lot_id and ml.quantity > 0
            )

            if not picking_move_lines:
                continue

            # 7. Process each product and its lots
            products_involved = picking_move_lines.mapped('product_id')
            for product in products_involved:
                # Find the corresponding raw material move on the MO
                mo_raw_moves = mo.move_raw_ids.filtered(
                    lambda m: m.product_id == product and m.state not in ('done', 'cancel')
                )
                if not mo_raw_moves:
                    continue

                # Typically there's one raw move per product on an MO
                mo_raw_move = mo_raw_moves[0]

                # Calculate how much is already reserved on this MO raw move
                already_reserved = sum(mo_raw_move.move_line_ids.mapped('quantity'))
                remaining_demand = mo_raw_move.product_uom_qty - already_reserved

                # If the MO is already fully reserved for this product, skip
                if remaining_demand <= 0:
                    continue

                # Prepare move line values to reserve the exact lots sent to production
                # WITHOUT clearing existing reservations
                vals_list = []
                relevant_move_lines = picking_move_lines.filtered(lambda ml: ml.product_id == product)

                for ml in relevant_move_lines:
                    if remaining_demand <= 0:
                        break  # Stop if we've fulfilled the MO's remaining demand

                    # Only reserve up to the remaining demand to avoid over-reserving
                    qty_to_reserve = min(ml.quantity, remaining_demand)

                    vals_list.append({
                        'move_id': mo_raw_move.id,
                        'product_id': product.id,
                        'lot_id': ml.lot_id.id,
                        'quantity': qty_to_reserve,
                        'product_uom_id': ml.product_uom_id.id,
                        'location_id': mo_raw_move.location_id.id,
                        'location_dest_id': mo_raw_move.location_dest_id.id,
                    })

                    remaining_demand -= qty_to_reserve

                # Create the explicit move lines with the lots
                if vals_list:
                    self.env['stock.move.line'].create(vals_list)
                    # Re-assign to update the MO move state and reserved quantities
                    mo_raw_move._action_assign()

        return res