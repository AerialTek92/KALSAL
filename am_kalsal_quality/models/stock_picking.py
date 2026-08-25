from datetime import datetime
from odoo import models, fields, api,_
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    # ADDED: 'qc_passed' state before 'done'
    state = fields.Selection(
        selection_add=[
            ('vehicle_inspection', 'Vehicle Inspection'),
            ('inspection_passed', 'Vehicle Passed Inspection'),
            ('assigned',),
            ('inspection_failed', 'Vehicle Failed Inspection'),
            ("qc_pending", 'Waiting for QC'),
            ('qc_passed', 'QC Passed'),  # <--- NEW STATE ADDED HERE
            ('done',),
            ("qc_failed", 'QC Failed')
        ],
    )

    vehicle_id = fields.Char(related="vehicle_inspection_id.vehicle_id", string="Vehicle ID")

    passed_qc_ids = fields.One2many(
        'kalsal.quality.check',
        'picking_ids',
        string='Passed QC Record Ids'
    )

    inv_no = fields.Char(string="Invoice No.")
    inv_date = fields.Date(string="Invoice Date")
    challan_no = fields.Char(string="Challan No.")
    challan_date = fields.Date(string="Challan Date")

    passed_qc_id = fields.Many2many(
        'kalsal.quality.check',
    )

    qc_ids = fields.One2many(
        'kalsal.quality.check',
        'picking_ids',
        string='QC Record Ids'
    )

    vehicle_inspection_id = fields.Many2one('vehicle.inspection', string="Vehicle Inspection", copy=False)

    # === NEW: Linked Documents for Next Transfer ===
    linked_purchase_id = fields.Many2one(
        'purchase.order',
        string='Source PO',
        compute='_compute_linked_documents',
        store=True
    )
    previous_transfer_id = fields.Many2one(
        'stock.picking',
        string='Previous Transfer',
        compute='_compute_linked_documents',
        store=True
    )

    @api.depends('move_ids', 'move_ids.move_orig_ids', 'move_ids.move_orig_ids.purchase_line_id')
    def _compute_linked_documents(self):
        for picking in self:
            po = self.env['purchase.order']
            prev_pick = self.env['stock.picking']

            # move_orig_ids links the current move back to the move that generated it (the GRN move)
            orig_moves = picking.move_ids.mapped('move_orig_ids')
            if orig_moves:
                prev_pick = orig_moves.mapped('picking_id')[:1]
                pos = orig_moves.mapped('purchase_line_id.order_id')
                if pos:
                    po = pos[0]

            picking.previous_transfer_id = prev_pick.id if prev_pick else False
            picking.linked_purchase_id = po.id if po else False

    def button_inspect(self):
        self.ensure_one()
        if self.vehicle_inspection_id:
            raise UserError("Inspection Already in Progress Kindly check the linked Vehicle Inspection Record.")

        material_names = ', '.join(self.move_ids.mapped('product_id').mapped('name'))

        inspection_vals = {
            'partner_id': self.partner_id.id if self.partner_id else False,
            'material_name': material_names,
            'inspection_date': fields.Date.today(),
            'inspector_id': self.env.user.id,
            'picking_id': self.id,
        }

        inspection = self.env['vehicle.inspection'].create(inspection_vals)
        self.vehicle_inspection_id = inspection.id

        return {
            'type': 'ir.actions.act_window',
            'name': 'Vehicle Inspection',
            'res_model': 'vehicle.inspection',
            'res_id': inspection.id,
            'view_mode': 'form',
            'target': 'current',
        }

    is_dest_wh_stock = fields.Boolean(
        string='Is Destination WH/Stock',
        compute='_compute_is_dest_wh_stock'
    )

    def button_validate(self):
        # 1. PRE-VALIDATION CHECKS: Run these BEFORE calling super()
        for picking in self:
            for qc in picking.qc_ids:
                if qc.state not in ['waiting_delay', 'pass', 'fail', '2nd_in_progress', 'cancel']:
                    raise UserError(
                        _("QC for %s is in progress. Complete all the QC's and validate to further transfer product to main warehouse.") % qc.product_id.name)

            # If the picking passed QC, revert state to 'assigned'
            # so standard Odoo allows the validation to proceed.
            if picking.state == 'qc_passed':
                picking.state = 'assigned'

        # 2. RUN STANDARD ODOO VALIDATION
        res = super().button_validate()

        # 3. POST-VALIDATION LOGIC: Merge lines and trigger PR checks
        for picking in self:
            if picking.picking_type_id.code == 'incoming':
                # Group all detailed operations (move lines) by product
                for line in picking.move_ids:
                    if line.accepted_qty > 0 and (line.product_uom_qty == line.quantity) and not picking.backorder_id:
                        line.quantity = line.accepted_qty

                lines_by_product = {}
                for line in picking.move_line_ids:
                    if not line.product_id:
                        continue
                    if line.product_id.id not in lines_by_product:
                        lines_by_product[line.product_id.id] = self.env['stock.move.line']
                    lines_by_product[line.product_id.id] |= line

                # If a product has more than one move line, merge them!
                for prod_id, lines in lines_by_product.items():
                    if len(lines) > 1:
                        main_line = lines.filtered(lambda l: l.lot_id)[:1] or lines[0]
                        other_lines = lines - main_line

                        for other in other_lines:
                            main_line.quantity += other.quantity
                            if not main_line.lot_id and other.lot_id:
                                main_line.lot_id = other.lot_id.id

                            if other.move_id and not other.move_id.move_line_ids:
                                other.move_id.unlink()

                            other.unlink()

        # 4. CHECK PR STOCK STATUS (Trigger _check_stock_status)
        # Only look at pickings that successfully reached 'done'
        done_pickings = self.filtered(lambda p: p.state == 'done' and p.previous_transfer_id)
        if done_pickings:
            # Find POs related to these done pickings
            pos = done_pickings.linked_purchase_id

            # Find PRs linked to these POs
            prs = pos.mapped('pr_order_id')

            # Run the stock status check on those PRs
            for pr in prs:
                pr._check_stock_status()

        return res
    @api.depends('location_dest_id', 'picking_type_id.warehouse_id')
    def _compute_is_dest_wh_stock(self):
        for picking in self:
            warehouse = picking.picking_type_id.warehouse_id
            if warehouse and picking.location_dest_id.id == warehouse.lot_stock_id.id:
                picking.is_dest_wh_stock = True
            else:
                picking.is_dest_wh_stock = False

    qc_count = fields.Integer(string="Quality Checks Count", compute="_compute_qc_count")

    is_storage_operation = fields.Boolean(
        compute='_compute_is_storage_operation',
        depends=['picking_type_id']  # Tells the UI to watch this field
    )

    @api.depends('picking_type_id')  # Triggers calculations in the web browser
    def _compute_is_storage_operation(self):
        storage_type = self.env.ref('am_kalsal_quality.picking_type_storage', raise_if_not_found=False)

        for rec in self:
            # Fallback check if XML ID configuration is not found yet
            if storage_type:
                rec.is_storage_operation = (rec.picking_type_id == storage_type)
            else:
                # Fallback to string check to prevent interface breakages
                rec.is_storage_operation = rec.picking_type_id.name == 'Storage'

    def po_view(self):
        self.ensure_one()
        return {
            'name': 'Purchase Order',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': self.purchase_id.id,
            'target': 'current',
        }

    @api.depends('qc_ids')
    def _compute_qc_count(self):
        for picking in self:
            picking.qc_count = len(picking.qc_ids)

    def action_view_passed_qc(self):
        self.ensure_one()
        return {
            'name': 'Kalsal QC Records',
            'type': 'ir.actions.act_window',
            'res_model': 'kalsal.quality.check',
            'view_mode': 'form',
            'res_id': self.passed_qc_id.id,
            'context': self.env.context,
        }

    def action_view_qc_records(self):
        self.ensure_one()
        qc = self.env['kalsal.quality.check']
        if self.qc_ids:
            qc |= self.qc_ids

        if len(qc) == 1:
            return {
                'name': 'Kalsal QC Records',
                'type': 'ir.actions.act_window',
                'res_model': 'kalsal.quality.check',
                'view_mode': 'form',
                'res_id': self.qc_ids.id,
                'context': self.env.context,
            }

        return {
            'name': 'Quality Checks',
            'type': 'ir.actions.act_window',
            'res_model': 'kalsal.quality.check',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.qc_ids.ids)],
            'context': {
                'default_picking_ids': [(4, self.id)],
                'default_partner_id': self.partner_id.id,
            }
        }

    def action_view_inspection(self):
        self.ensure_one()
        return {
            'name': 'Vehicle Inspection',
            'type': 'ir.actions.act_window',
            'res_model': 'vehicle.inspection',
            'view_mode': 'form',
            'res_id': self.vehicle_inspection_id.id,
            'target': 'current',
        }
    def action_view_prev_transfer(self):
        self.ensure_one()
        return {
            'name': 'Stock Picking',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.previous_transfer_id.id,
            'target': 'current',
        }
    def action_view_purchase(self):
        self.ensure_one()
        return {
            'name': 'Purchase Order',
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': self.linked_purchase_id.id,
            'target': 'current',
        }

    def initiate_qc(self):
        """Generate individual QC records based on stock moves (product lines),
        linking ALL associated lots to the same QC."""
        qc_env = self.env['kalsal.quality.check']

        for picking in self:
            picking.state = 'qc_pending'
            qc_ids_to_link = []

            # Iterate over `move_ids` (the main demand lines) instead of `move_line_ids`
            for move in picking.move_ids:
                if not move.quantity:
                    raise UserError(_('Picking Product %s must have quantity.') % move.product_id.name)
                if not move.product_id:
                    continue

                # Check if a QC already exists for this product line to prevent duplicates
                existing_qc = qc_env.search([
                    ('picking_ids', 'in', picking.id),
                    ('product_id', '=', move.product_id.id),
                ], limit=1)

                if not existing_qc:
                    # Gather all lots associated with this specific move
                    lots = move.move_line_ids.mapped('lot_id')

                    # 1. Create the QC Record WITHOUT the lots first to avoid DatatypeMismatch
                    new_qc = qc_env.create({
                        'product_id': move.product_id.id,
                        'partner_id': picking.partner_id.id,
                        'picking_ids': picking.id,
                        'state': 'draft',
                    })

                    # 2. Assign the lots using the recordset directly
                    if lots:
                        new_qc.lot_ids = lots

                    qc_ids_to_link.append(new_qc.id)
                else:
                    # If it already exists, ensure its ID is in our list
                    qc_ids_to_link.append(existing_qc.id)

            # Link all collected QC records to the picking's qc_ids field
            if qc_ids_to_link:
                picking.qc_ids = [(4, qc_id) for qc_id in qc_ids_to_link]

    @api.depends('move_ids.state', 'picking_type_id.use_create_lots', 'picking_type_id.use_existing_lots')
    def _compute_state(self):
        # 1. Store the current states before Odoo recalculates them
        states_before = {p.id: p.state for p in self}

        # 2. Let standard Odoo compute the state
        super()._compute_state()

        # 3. Restore custom states if Odoo tried to overwrite them
        for picking in self:
            previous_state = states_before.get(picking.id)

            # If the picking was in one of our custom QC/Inspection states
            if previous_state in [
                'vehicle_inspection',
                'inspection_passed',
                'inspection_failed',
                'qc_pending',
                'qc_passed',
                'qc_failed'
            ]:
                # Only allow standard Odoo to change it if it's moving to 'done' or 'cancel'
                # Otherwise, force it back to the custom state!
                if picking.state not in ('done', 'cancel'):
                    picking.state = previous_state

            # OPTIONAL: If you also want to prevent a 'draft' picking from becoming
            # 'confirmed' or 'assigned' just by typing a quantity, uncomment this block:
            #
            elif previous_state == 'draft' and picking.state in ('confirmed', 'assigned'):
                picking.state = 'draft'

class StockMove(models.Model):
    _inherit = 'stock.move'

    purchase_id = fields.Many2one('purchase.order', related='purchase_line_id.order_id', store=True)
    purchase_order_date = fields.Datetime(related='purchase_line_id.order_id.date_approve', store=True)

    challan_qty = fields.Float(string='Challan Qty', store=True)

    short_qty = fields.Float(
        string='Short Qty',
        store=True,
        compute='_compute_short_excess_qty',
    )

    excess_qty = fields.Float(
        string='Excess Qty',
        store=True,
        compute='_compute_short_excess_qty',
    )

    qc_updated = fields.Boolean(string='QC Updated', default=False)

    rejected_qty = fields.Float(string='Rejected Qty', store=True)
    accepted_qty = fields.Float(string='Accepted Qty', store=True)




    @api.depends('product_uom_qty', 'quantity')
    def _compute_short_excess_qty(self):
        for move in self:
            demand = move.product_uom_qty or 0.0
            done = move.quantity or 0.0
            if done < demand:
                move.short_qty = int(demand - done)
                move.excess_qty = 0
            elif done > demand:
                move.short_qty = 0
                move.excess_qty = int(done - demand)
            else:
                move.short_qty = 0
                move.excess_qty = 0


class StockBackorderConfirmation(models.TransientModel):
    _inherit = 'stock.backorder.confirmation'

    def process(self):
        # 1. Run the native Odoo backorder generation code first
        res = super(StockBackorderConfirmation, self).process()

        # 2. Iterate through the wizard records
        for confirmation in self:
            # FIX: Changed 'pickings_to_backorder' to 'pick_ids'
            for picking in confirmation.pick_ids:
                # Find any newly generated child backorders referencing this picking
                backorders = self.env['stock.picking'].search([
                    ('backorder_id', '=', picking.id)
                ])

                # 3. Apply your custom hold state to the split backorders
                if backorders:
                    backorders.write({'state': 'vehicle_inspection'})

                    # Log audit history to the chatter
                    for bo in backorders:
                        bo.message_post(body="This backorder has been placed on Custom Hold.")
        return res


