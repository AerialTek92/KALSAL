from odoo import models, fields, api, _
from odoo.exceptions import UserError


class PartialAcceptanceWizard(models.TransientModel):
    _name = 'kalsal.partial.acceptance.wizard'
    _description = 'Partial Acceptance by Lot Wizard'

    qc_id = fields.Many2one(
        'kalsal.quality.check',
        string='Quality Check',
        required=True,
        ondelete='cascade'
    )
    line_ids = fields.One2many(
        'kalsal.partial.acceptance.wizard.line',
        'wizard_id',
        string='Lots to Accept'
    )

    def action_confirm_partial_acceptance(self):
        self.ensure_one()


        # Validate lines
        for line in self.line_ids:
            if line.accepted_qty <= 0 or line.accepted_qty > line.total_qty:
                raise UserError(_(
                    "Accepted quantity for lot %s should not be 0 and should be less than %s, if you want to return you can fail the QC to make a total return of the Receipt."
                ) % (line.lot_id.name or 'N/A', line.total_qty))

        # Pass data to the QC record for processing
        lot_data = []
        for line in self.line_ids:
            lot_data.append({
                'move_line_id': line.move_line_id.id,
                'lot_id': line.lot_id.id,
                'accepted_qty': line.accepted_qty,
                'rejected_qty': line.rejected_qty,
                'uom_id': line.move_line_id.product_uom_id.id,
            })


        self.qc_id.process_lot_based_partial_acceptance(lot_data)
        return {'type': 'ir.actions.act_window_close'}


class PartialAcceptanceWizardLine(models.TransientModel):
    _name = 'kalsal.partial.acceptance.wizard.line'
    _description = 'Partial Acceptance Lot Line'

    wizard_id = fields.Many2one(
        'kalsal.partial.acceptance.wizard',
        string='Wizard',
        ondelete='cascade'
    )
    move_line_id = fields.Many2one(
        'stock.move.line',
        string='Move Line',
        # required=True
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        readonly=True
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lot/Serial',
        # readonly=True
    )
    total_qty = fields.Float(
        string='Total Received Qty',
        readonly=True
    )
    accepted_qty = fields.Float(
        string='Accepted Qty',
        default=0.0
    )
    rejected_qty = fields.Float(
        string='Rejected Qty',
        compute='_compute_rejected_qty',
        store=True
    )

    @api.depends('total_qty', 'accepted_qty')
    def _compute_rejected_qty(self):
        for rec in self:
            rec.rejected_qty = max(0.0, rec.total_qty - rec.accepted_qty)