from odoo import models, fields, api, _


class MaterialRequisitionSlip(models.Model):
    _inherit = 'material.requisition.slip'

    # ---------- CONNECTIVITY ONLY ----------
    # Reverse link: which Mixing Slips were built from this MRS
    mixing_slip_ids = fields.One2many(
        'mixing.slip', 'mrs_id', string='Mixing Slips')

    mixing_slip_count = fields.Integer(
        string='Mixing Slip Count', compute='_compute_mixing_slip_count')

    # NEW: Track if the internal transfer is validated
    picking_done = fields.Boolean(
        string='Picking Validated',
        compute='_compute_picking_done',
        store=False)

    @api.depends('mixing_slip_ids')
    def _compute_mixing_slip_count(self):
        for rec in self:
            rec.mixing_slip_count = len(rec.mixing_slip_ids)

    @api.depends('picking_id', 'picking_id.state')
    def _compute_picking_done(self):
        for rec in self:
            rec.picking_done = rec.picking_id and rec.picking_id.state == 'done'

    def action_open_mixing_slip(self):
        """Next-step button: open the Mixing Slip for this MRS,
        or create it pre-filled (with live MRS-linked lines) if none exists."""
        self.ensure_one()

        slips = self.env['mixing.slip'].search(
            [('mrs_id', '=', self.id)], order='id desc')

        if not slips:
            slips = self.env['mixing.slip'].create({
                'sale_order_id': self.sale_order_id.id or False,
                'recipe_product_id': self.recipe_product_id.id or False,
                'mrs_id': self.id,
                # One line per MRS line; Issued/S.No/Code/UOM flow via relateds
                'line_ids': [
                    (0, 0, {'mrs_line_id': line.id})
                    for line in self.recipe_line_ids
                ],
            })

        if len(slips) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Mixing Slip'),
                'res_model': 'mixing.slip',
                'res_id': slips.id,
                'view_mode': 'form',
                'target': 'current',
            }

        # Safety: if more than one slip exists, show them as a list
        return {
            'type': 'ir.actions.act_window',
            'name': _('Mixing Slips'),
            'res_model': 'mixing.slip',
            'view_mode': 'list,form',
            'domain': [('id', 'in', slips.ids)],
        }