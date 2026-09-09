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

    allowed_recipe_product_ids = fields.Many2many(
        'product.product',
        string='Allowed Recipes',
        compute='_compute_allowed_recipe_product_ids'
    )

    recipe_product_id = fields.Many2one(
        'product.product', string='Recipe Name', tracking=True,
        help="Select a product (recipe) that exists in the chosen Sale Order.")

    kgs = fields.Float(
        string='Remaining Qty to Produce', tracking=True,
        help="Quantity still left to produce for this recipe on this Sale "
             "Order, as of when this MRS batch was created. Splits into "
             "'Qty Producing' for this specific batch.")

    remaining_qty = fields.Float(
        string='Remaining After This Batch', compute='_compute_produced_remaining_qty',
        help="What will still be left to produce once this batch is done.")

    qty_producing = fields.Float(
        string='Qty Producing', tracking=True,
        help="Quantity being produced in THIS batch. Drives the BOM "
             "tailoring and stays in sync with 'Qty Producing' on the "
             "linked Manufacturing Order, in both directions.")

    produced_qty = fields.Float(
        string='Already Produced', compute='_compute_produced_remaining_qty',
        help="Sum of Qty Producing across this recipe's other "
             "confirmed/done MRS batches on this Sale Order.")

    to_department_id = fields.Many2one('hr.department', string='To Department', tracking=True)

    uom_id = fields.Many2one('uom.uom', string='UOM', store=True, readonly=True)

    bom_id = fields.Many2one('mrp.bom', string='Bill of Material', store=True, readonly=True)

    mrp_production_id = fields.Many2one(
        'mrp.production', string='Manufacturing Order', tracking=True, copy=False,
        help="Auto-resolved from the Sale Order, Recipe Product and BOM. "
             "'Qty Producing' here and on this MO are kept in sync.")

    recipe_line_ids = fields.One2many(
        'material.requisition.line', 'slip_id', string='Recipe Lines', store=True)

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

    allowed_sale_order_ids = fields.Many2many(
        'sale.order', string='Sale Orders With Pending Requisition',
        compute='_compute_allowed_sale_order_ids')

    @api.depends()
    def _compute_allowed_sale_order_ids(self):
        # No stored dependency on purpose: this must reflect the CURRENT
        # state of every MRS in the system (which product on which SO has
        # already had its full qty covered), not just fields on this record.
        candidate_sos = self.env['sale.order'].search([('stock_ready', '=', True)])
        for rec in self:
            allowed = self.env['sale.order']
            for so in candidate_sos:
                if not rec._so_has_pending_requisition(so):
                    continue
                allowed |= so
            rec.allowed_sale_order_ids = allowed

    def _so_has_pending_requisition(self, sale_order):
        """True if AT LEAST ONE product line on this SO still has qty
        remaining to be requisitioned (i.e. not yet covered by non-cancelled
        MRS batches). False only when every line is fully covered."""
        self.ensure_one() if self else None
        for so_line in sale_order.order_line:
            if not so_line.product_id:
                continue

            # in _so_has_pending_requisition
            domain = [
                ('sale_order_id', '=', sale_order.id),
                ('recipe_product_id', '=', so_line.product_id.id),
                ('state', 'in', ('confirmed', 'done')),
            ]
            if self.id:
                domain.append(('id', '!=', self.id))

            other_mrs = self.env['material.requisition.slip'].search(domain)
            requisitioned_qty = sum(other_mrs.mapped('qty_producing'))

            if float_compare(requisitioned_qty, so_line.product_uom_qty,
                             precision_digits=2) < 0:
                return True  # this line still has qty remaining
        return False  # every line fully covered — SO is done


    @api.depends('kgs', 'qty_producing', 'sale_order_id', 'recipe_product_id', 'state')
    def _compute_produced_remaining_qty(self):
        for rec in self:
            if not (rec.sale_order_id and rec.recipe_product_id):
                rec.produced_qty = 0.0
                rec.remaining_qty = rec.kgs
                continue
            siblings = self.env['material.requisition.slip'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('recipe_product_id', '=', rec.recipe_product_id.id),
                ('state', 'in', ('confirmed', 'done')),
                ('id', '!=', rec.id or 0),
            ])
            rec.produced_qty = sum(siblings.mapped('qty_producing'))
            rec.remaining_qty = max(rec.kgs - rec.produced_qty - rec.qty_producing, 0.0)

    # ---------- Compute Allowed Products ----------

    @api.depends('kgs', 'qty_producing', 'sale_order_id', 'recipe_product_id', 'state')
    def _compute_produced_remaining_qty(self):
        for rec in self:
            if not (rec.sale_order_id and rec.recipe_product_id):
                rec.produced_qty = 0.0
                rec.remaining_qty = rec.kgs
                continue
            siblings = self.env['material.requisition.slip'].search([
                ('sale_order_id', '=', rec.sale_order_id.id),
                ('recipe_product_id', '=', rec.recipe_product_id.id),
                ('state', 'in', ('confirmed', 'done')),
                ('id', '!=', rec.id or 0),
            ])
            rec.produced_qty = sum(siblings.mapped('qty_producing'))
            # kgs is already "remaining before this batch" — just subtract
            # what THIS batch is about to produce, don't double-subtract.
            rec.remaining_qty = max(rec.kgs - rec.qty_producing, 0.0)

    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        for rec in self:
            if not rec.sale_order_id:
                rec.allowed_recipe_product_ids = False
                continue

            all_so_products = rec.sale_order_id.order_line.mapped('product_id')
            fully_requisitioned = self.env['product.product']

            for product in all_so_products:
                so_line = rec.sale_order_id.order_line.filtered(
                    lambda l: l.product_id == product)[:1]
                if not so_line:
                    continue

                # in _compute_allowed_recipe_product_ids
                domain = [
                    ('sale_order_id', '=', rec.sale_order_id.id),
                    ('recipe_product_id', '=', product.id),
                    ('state', 'in', ('confirmed', 'done')),
                ]
                if rec.id:
                    domain.append(('id', '!=', rec.id))

                other_mrs = self.env['material.requisition.slip'].search(domain)
                requisitioned_qty = sum(other_mrs.mapped('qty_producing'))

                if float_compare(requisitioned_qty, so_line.product_uom_qty,
                                  precision_digits=2) >= 0:
                    fully_requisitioned |= product

            rec.allowed_recipe_product_ids = all_so_products - fully_requisitioned

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
            self.kgs = 0.0
            self.qty_producing = 0.0
            self.bom_id = False
            self.mrp_production_id = False
            self.recipe_line_ids = [(5, 0, 0)]

    # ---------- Onchange: Recipe product -> UOM, Qty, BOM lines, MO link ----------
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

        # in _onchange_recipe_product_id
        other_mrs = self.env['material.requisition.slip'].search([
            ('sale_order_id', '=', self.sale_order_id.id),
            ('recipe_product_id', '=', self.recipe_product_id.id),
            ('state', 'in', ('confirmed', 'done')),
        ])
        already_producing = sum(other_mrs.mapped('qty_producing'))

        # kgs is now a SNAPSHOT of what's left to produce at the moment this
        # MRS is created — not the fixed original order total. It only equals
        # the full order qty when this is the FIRST MRS for this product.
        self.kgs = max(line.product_uom_qty - already_producing, 0.0)

        # Default this batch to producing everything that's currently left;
        # the user reduces it manually to split into smaller pieces.
        self.qty_producing = self.kgs

        # Safe BOM search compatible with Odoo 19
        bom = self.env['mrp.bom'].search([
            ('product_id', '=', self.recipe_product_id.id),
            ('id', '=', line.bom_id.id)
        ], limit=1)
        if not bom:
            bom = self.env['mrp.bom'].search([
                ('product_tmpl_id', '=', self.recipe_product_id.product_tmpl_id.id),
                ('id', '=', line.bom_id.id)
            ], limit=1)

        if not bom:
            self.bom_id = False
            self.mrp_production_id = False
            self.recipe_line_ids = [(5, 0, 0)]
            return {
                'warning': {
                    'title': _('No BOM'),
                    'message': _('No Bill of Material found for the selected recipe product.'),
                }
            }

        self.bom_id = bom

        # Resolve and STORE the MO link once, instead of re-guessing it later.
        self.mrp_production_id = self.env['mrp.production'].search([
            ('product_id', '=', self.recipe_product_id.id),
            ('bom_id', '=', bom.id),
            ('origin', '=', self.sale_order_id.name),
            ('state', 'in', ['confirmed', 'progress', 'to_close']),
        ], limit=1)

        self._populate_recipe_lines()

    @api.onchange('qty_producing', 'kgs')
    def _onchange_batch_qty(self):
        if self.bom_id:
            self._populate_recipe_lines()

    def _populate_recipe_lines(self):
        self.ensure_one()
        lines = [(5, 0, 0)]
        if not self.bom_id:
            self.recipe_line_ids = lines
            return

        bom = self.bom_id

        def _to_bom_uom(qty):
            try:
                return self.uom_id._compute_quantity(
                    qty or 0.0, bom.product_uom_id) if self.uom_id else (qty or 0.0)
            except Exception:
                return qty or 0.0

        producing_factor = (
            _to_bom_uom(self.qty_producing) / bom.product_qty
        ) if bom.product_qty else 0.0
        total_factor = (
            _to_bom_uom(self.kgs) / bom.product_qty
        ) if bom.product_qty else 0.0

        sno = 1
        for bl in bom.bom_line_ids:
            lines.append((0, 0, {
                'sno': sno,
                'product_id': bl.product_id.id,
                'item_code': bl.product_id.default_code or '',
                'item_description': bl.product_id.name or '',
                'uom_id': bl.product_uom_id.id,
                'quantity_required': bl.product_qty * producing_factor,      # this batch
                'quantity_required_total': bl.product_qty * total_factor,    # whole order
                'quantity_issued': 0.0,
                'remarks': '',
            }))
            sno += 1
        self.recipe_line_ids = lines

    @api.constrains('qty_producing')
    def _check_qty_producing_positive(self):
        for rec in self:
            if rec.qty_producing <= 0:
                raise UserError(_("Qty Producing must be greater than zero."))

    # ---------- MRS -> MO sync ----------
    def write(self, vals):
        res = super().write(vals)
        if 'qty_producing' in vals and not self.env.context.get('skip_mo_sync'):
            for rec in self:
                if rec.bom_id:
                    rec._populate_recipe_lines()
                rec._sync_qty_producing_to_mo()
        return res

    def _sync_qty_producing_to_mo(self):
        self.ensure_one()
        mo = self.mrp_production_id
        if not mo or mo.state in ('done', 'cancel'):
            return
        if float_compare(mo.qty_producing, self.qty_producing, precision_digits=2) != 0:
            mo.with_context(skip_mrs_sync=True).write({'qty_producing': self.qty_producing})

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

            # NEW: catch manually-added lines with no product before touching stock APIs
            incomplete_lines = rec.recipe_line_ids.filtered(
                lambda l: not l.product_id or not l.uom_id)
            if incomplete_lines:
                bad_labels = ', '.join(
                    l.item_description or l.item_code or _('(unnamed line)')
                    for l in incomplete_lines
                )
                raise UserError(_(
                    "The following line(s) have no linked product/UOM and "
                    "can't be requisitioned: %s.\n\n"
                    "Recipe lines must come from the BOM — please remove any "
                    "manually-typed lines, or re-select the Recipe Name to "
                    "regenerate the lines from the BOM."
                ) % bad_labels)

            warehouse = Warehouse.search([('company_id', '=', rec.company_id.id)], limit=1)
            picking_type = PickingType.search([
                ('code', '=', 'internal'),
                ('warehouse_id', '=', warehouse.id)
            ], limit=1)

            source_location = warehouse.lot_stock_id

            dest_location = StockLocation.search([
                ('usage', '=', 'production'),
                ('id', 'child_of', warehouse.view_location_id.id)
            ], limit=1)

            if not dest_location:
                dest_location = StockLocation.search([
                    ('usage', '=', 'production'),
                    ('company_id', 'in', [rec.company_id.id, False])
                ], limit=1)

            if not source_location or not dest_location:
                raise UserError(
                    _("Source or Destination location is missing. Please configure your warehouse locations."))

            move_lines = []
            precision = self.env['decimal.precision'].precision_get(
                'Product Unit of Measure')

            for line in rec.recipe_line_ids:
                is_different = float_compare(line.quantity_issued, line.quantity_required,
                                             precision_digits=precision) != 0

                if is_different and not line.remarks:
                    raise UserError(
                        _("Quantity issued for product %s is less than or equal to Required qty for making this product. Kindly add Remarks.") % line.product_id.name
                    )

                if line.quantity_issued <= 0:
                    continue

                available_qty = self.env['stock.quant']._get_available_quantity(
                    line.product_id, source_location
                )
                actual_qty = min(line.quantity_issued, available_qty)

                if actual_qty <= 0:
                    continue

                if actual_qty < line.quantity_issued and not line.remarks:
                    raise UserError(_(
                        "Only %s of %s is available in stock for %s. Please reduce the "
                        "Issued quantity or add remarks explaining the shortfall."
                    ) % (available_qty, line.quantity_issued, line.product_id.name))

                move_lines.append((0, 0, {
                    'product_id': line.product_id.id,
                    'product_uom': line.uom_id.id,
                    'product_uom_qty': actual_qty,
                    'location_id': source_location.id,
                    'location_dest_id': dest_location.id,
                }))

            if move_lines:
                picking_vals = {
                    'partner_id': rec.sale_order_id.partner_id.id if rec.sale_order_id else False,
                    'picking_type_id': picking_type.id,
                    'origin': rec.name,
                    'location_id': source_location.id,
                    'location_dest_id': dest_location.id,
                    'move_ids': move_lines,
                }
                picking = Picking.create(picking_vals)
                picking.action_confirm()

                for move in picking.move_ids:
                    match_line = rec.recipe_line_ids.filtered(lambda l: l.product_id.id == move.product_id.id)
                    if match_line:
                        qty = match_line[0].quantity_issued
                        move.write({
                            'product_uom_qty': qty,
                            'quantity': qty
                        })

                rec.picking_id = picking.id
            else:
                # NEW: don't silently confirm with nothing transferred
                raise UserError(_(
                    "No Internal Transfer was created: none of the requisitioned "
                    "materials have available stock at %s, or no quantities were "
                    "entered in the 'Consumed' column. Please check stock levels "
                    "and issued quantities before confirming."
                ) % source_location.display_name)

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
    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    item_code = fields.Char(string='Item Code')
    item_description = fields.Char(string='Item Description')
    uom_id = fields.Many2one('uom.uom', string='UOM')
    quantity_required = fields.Float(string='Required', default=0.0)
    quantity_required_total = fields.Float(string='Total Required', default=0.0)
    quantity_issued = fields.Float(string='Issued', default=0.0)
    remarks = fields.Char(string='Remarks')

    to_consume_display = fields.Char(
        string='To Consume', compute='_compute_to_consume_display')

    @api.depends('quantity_required', 'quantity_required_total')
    def _compute_to_consume_display(self):
        for line in self:
            line.to_consume_display = "%.2f / %.2f" % (
                line.quantity_required, line.quantity_required_total)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    stock_ready = fields.Boolean(
        string='Stock Up',
        default=False,
        copy=False,
        store=True,
        help="True when all linked Purchase Orders for this SO are fully received in WH/Stock."
    )


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_assign(self, force_qty=False):
        if self.env.context.get('allow_raw_move_auto_assign'):
            return super()._action_assign(force_qty=force_qty)

        raw_moves = self.filtered(lambda m: m.raw_material_production_id)
        other_moves = self - raw_moves

        res = None
        if other_moves:
            res = super(StockMove, other_moves)._action_assign(force_qty=force_qty)
        return res


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        res = super().button_validate()

        if res is not True:
            return res

        MRS = self.env['material.requisition.slip']
        MO = self.env['mrp.production']
        StockLocation = self.env['stock.location']
        Warehouse = self.env['stock.warehouse']

        for picking in self:
            warehouse = Warehouse.search([('company_id', '=', picking.company_id.id)], limit=1)
            if not warehouse:
                continue

            source_location = warehouse.lot_stock_id
            dest_location = StockLocation.search([
                ('usage', '=', 'production'),
                ('id', 'child_of', warehouse.view_location_id.id)
            ], limit=1)

            if not (source_location and dest_location and
                    picking.location_id == source_location and
                    picking.location_dest_id == dest_location):
                continue

            mrs = MRS.search([('name', '=', picking.origin)], limit=1)
            if not mrs:
                continue

            mo = mrs.mrp_production_id or MO.search([
                ('product_id', '=', mrs.recipe_product_id.id),
                ('bom_id', '=', mrs.bom_id.id),
                ('origin', '=', mrs.sale_order_id.name),
                ('state', 'in', ['confirmed', 'progress', 'to_close'])
            ], limit=1)

            if mo:
                mo.action_assign_lots_to_mo_lines()
            else:
                return res

            return res

