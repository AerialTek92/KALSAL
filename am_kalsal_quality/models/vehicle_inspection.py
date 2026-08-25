from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


class VehicleInspection(models.Model):
    _name = 'vehicle.inspection'
    _description = 'Vehicle Inspection Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'inspection_date desc'

    # Shipment Details
    name = fields.Char(string='Doc #', required=True, copy=False, readonly=True,
                       default=lambda self: _('New'))

    reviewed_by = fields.Many2one('res.users', string="Reviewed By", tracking=True)
    picking_id = fields.Many2one('stock.picking', string="GRN / Picking", copy=False)

    inspection_date = fields.Datetime(string='Date', default=fields.Datetime.now, required=True)
    vehicle_id = fields.Char(string='Vehicle #', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Supplier / Customer', tracking=True)
    material_name = fields.Char(string='Material Name')
    inspector_id = fields.Many2one('res.users', string='Inspector / QC', default=lambda self: self.env.user, tracking=True)

    # State Management
    state = fields.Selection([
        ('draft', 'Draft'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft', tracking=True)

    # Inspection Checklist (YES / NO fields based on image)
    check_dust_dirt = fields.Selection([('yes', 'YES'), ('no', 'NO')], string='Dust / Dirt', default='no')
    check_suspicious_items = fields.Selection([('yes', 'YES'), ('no', 'NO')], string='Suspicious Items', default='no')
    check_unusual_odor = fields.Selection([('yes', 'YES'), ('no', 'NO')], string='Unusual Odor', default='no')
    check_oil_spills = fields.Selection([('yes', 'YES'), ('no', 'NO')], string='Oil / Grease Spills', default='no')

    # Remarks & Notes
    remarks = fields.Text(string='Remarks / Notes')

    def action_view_picking(self):
        self.ensure_one()
        return {
            'name': 'Stock Picking',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.picking_id.id,  # This is the ID of the record you want to open
            'target': 'current',  # Use 'new' if you want it to open in a popup
        }

    # --- CRUD Methods ---
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('vehicle.inspection') or _('New')
        return super().create(vals_list)

    # --- Action Methods ---
    def action_start_inspection(self):
        self.state = 'in_progress'

    def action_complete_inspection(self):
        # 1. Ensure the method runs on a single record
        self.ensure_one()

        # Check if any of the inspection fields are explicitly set to 'yes'
        has_failed_check = any([
            self.check_dust_dirt == 'yes',
            self.check_suspicious_items == 'yes',
            self.check_unusual_odor == 'yes',
            self.check_oil_spills == 'yes',
        ])

        if has_failed_check and not self.remarks:
            raise UserError(
                _("You must provide remarks in the Notifications section if any check is marked as 'Yes'."))

        # Set the state to done
        self.state = 'passed'

        # 2. Process the linked picking
        picking = self.picking_id
        picking.state = 'inspection_passed'
        # if picking and picking.state != 'done':
        #
        #     # Step A: Push from 'draft' to 'confirmed'
        #     if picking.state == 'draft':
        #         picking.action_confirm()
        #
        #     # Step B: Push to 'assigned' (Ready state) to generate move lines
        #     if picking.state not in ('assigned', 'done'):
        #         picking.action_assign()

            # Step C: Set Done Quantities.
            # We loop over move_ids (the main lines) instead of move_line_ids. It's much safer.
            # for move in picking.move_ids:
            #     move.quantity = move.product_uom_qty
            #
            # # Step D: Call validate OUTSIDE the loop
            # res = picking.button_validate()

            # Step E: Catch and process any Odoo pop-up wizards automatically
            # if isinstance(res, dict) and res.get('res_model'):
            #     wizard_model = res['res_model']
            #     wizard_context = res.get('context', {})
            #
            #     if wizard_model == 'stock.immediate.transfer':
            #         wizard = self.env[wizard_model].with_context(wizard_context).create({})
            #         wizard.process()
            #     elif wizard_model == 'stock.backorder.confirmation':
            #         wizard = self.env[wizard_model].with_context(wizard_context).create({})
            #         wizard.process_cancel_backorder()  # Or process() if you want backorders

        # 3. Open the validated picking
        return {
            'type': 'ir.actions.act_window',
            'name': 'Stock Picking',
            'res_model': 'stock.picking',
            'res_id': self.picking_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_reset(self):
        self.ensure_one()
        self.state = 'draft'

    def action_fail_inspection(self):
        for record in self:

            # If a check is marked YES, but no remarks are provided, raise an error
            if not record.remarks:
                raise UserError(
                    _("You must provide a reason for failing inspection."))

            self.picking_id.action_cancel()
            self.picking_id.purchase_id.button_cancel()
            self.picking_id.purchase_id.button_draft()

            self.state = 'failed'

            # Return an action to open the newly created Vehicle Inspection form
            return {
                'type': 'ir.actions.act_window',
                'name': 'Stock Picking',
                'res_model': 'stock.picking',
                'res_id': self.picking_id.id,
                'view_mode': 'form',
                'target': 'current',
            }

    def action_cancel(self):
        self.state = 'cancelled'