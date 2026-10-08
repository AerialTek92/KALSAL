from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    vehicle_number = fields.Char(
        string='Vehicle Number', tracking=True, copy=False,
        help="Vehicle number for this delivery. Printed on the Delivery Challan.")

    sto_number = fields.Char(
        string='STO No', tracking=True, copy=False,
        help="STO reference for this delivery. Printed on the Delivery Challan.")

    def button_validate(self):
        """Mandatory gate: an outgoing delivery cannot be validated without
        Vehicle Number and STO No."""
        for pick in self:
            if pick.picking_type_id.code == 'outgoing':
                missing = [label for label, value in (
                    ('Vehicle Number', pick.vehicle_number),
                    ('STO No', pick.sto_number),
                ) if not value]
                if missing:
                    raise UserError(_(
                        "Delivery %s cannot be validated.\n\n"
                        "The following mandatory field(s) are empty:\n• %s")
                        % (pick.name, '\n• '.join(missing)))

        pickings_before = self
        res = super().button_validate()

        done_pickings = pickings_before.filtered(lambda p: p.state == 'done')
        for pick in done_pickings:
            fg_report = self.env['fg.reporting'].search([('picking_id', '=', pick.id)], limit=1)
            if fg_report:
                fg_report._notify_quality_users_transfer_validated()

        return res
    

    def _get_current_base_url(self):
        """URL of the server actually handling this request, falling back to
        detecting the machine's live network IP when there's no HTTP request
        context (e.g. a cron job)."""
        try:
            from odoo.http import request
            if request:
                return request.httprequest.host_url.rstrip('/')
        except RuntimeError:
            pass

        import socket
        ip = '127.0.0.1'
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError:
            pass

        port = self.env['ir.config_parameter'].sudo().get_param('http_port') or '8069'
        return f'http://{ip}:{port}'