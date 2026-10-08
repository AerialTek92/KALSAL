from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup


class SalesOrder(models.Model):
    _inherit = 'sale.order'

    x_budget_id = fields.Many2one('custom.budget', string='Master Forecast Budget')

    stock_ready = fields.Boolean(
        string='Stock Up',
        default=False,
        copy=False,
        store=True,
        help="True when all linked Purchase Orders for this SO are fully received in WH/Stock."
    )

    def action_custom_save(self):
        return True

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

    def _get_group_partners(self, group_xmlid):
        """Resolve a security group to its member partners, excluding Factory
        Admin/CEO and Operations users — they inherit department access as
        part of their broader role, but shouldn't be pulled into
        department-level operational notifications."""
        target_group = self.env.ref(group_xmlid, raise_if_not_found=False)
        if not target_group:
            return self.env['res.partner']

        excluded_groups = self.env['res.groups']
        for xmlid in ('fk_kalsal_security.group_factory_admin', 'fk_kalsal_security.group_operations'):
            grp = self.env.ref(xmlid, raise_if_not_found=False)
            if grp:
                excluded_groups |= grp

        users = self.env['res.users'].search([('all_group_ids', 'in', [target_group.id])])
        if excluded_groups:
            users = users.filtered(lambda u: not (u.all_group_ids & excluded_groups))
        return users.mapped('partner_id')

    # ------------------------------------------------------------------
    # HTML fragment builders — each returns a Markup object from the
    # start, so nothing downstream re-escapes the tags.
    # ------------------------------------------------------------------
    def _build_mo_link_html(self):
        self.ensure_one()
        mos = self.env['mrp.production'].search([('origin', '=', self.name)])
        if not mos:
            return _("No Manufacturing Orders have been generated for this order yet.")

        base_url = self._get_current_base_url()

        if len(mos) == 1:
            url = f"{base_url}/web#id={mos.id}&model=mrp.production&view_type=form"
            label = _("Click here to open the Manufacturing Order")
        else:
            action = self.env['ir.actions.act_window'].sudo().create({
                'name': _('Manufacturing Orders - %s') % self.name,
                'res_model': 'mrp.production',
                'view_mode': 'list,form',
                'domain': str([('origin', '=', self.name)]),
            })
            url = f"{base_url}/web#action={action.id}&model=mrp.production&view_type=list"
            label = _("Click here to view the linked Manufacturing Orders")

        return Markup("<a href='%s' target='_blank'><b>%s</b></a>.") % (url, label)

    def _build_budget_link_html(self):
        self.ensure_one()
        if not self.x_budget_id:
            return _("No Forecast Budget has been generated for this order yet.")

        base_url = self._get_current_base_url()
        url = f"{base_url}/web#id={self.x_budget_id.id}&model=custom.budget&view_type=form"
        label = _("Click here to open the Forecast Budget")
        return Markup("<a href='%s' target='_blank'><b>%s</b></a>.") % (url, label)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _notify_group_so_confirmed(self, group_xmlid):
        target_partners = self._get_group_partners(group_xmlid)
        if not target_partners:
            return

        notify_ctx = dict(self.env.context, mail_notify_force_send=False)

        for order in self:
            mo_link_html = order._build_mo_link_html()
            budget_link_html = order._build_budget_link_html()

            title = _("Sale Order Confirmed")
            plain_msg = _(
                "Sale Order %s has been confirmed. Manufacturing Orders and the Forecast Budget are ready for review."
            ) % order.name
            html_msg = Markup(
                "Sale Order <b>%s</b> has been confirmed.<br/><br/>%s<br/>%s"
            ) % (order.name, mo_link_html, budget_link_html)

            if hasattr(order, 'message_notify'):
                order.with_context(notify_ctx).message_notify(
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            else:
                self.env['mail.thread'].with_context(notify_ctx).message_notify(
                    model='sale.order', res_id=order.id,
                    partner_ids=target_partners.ids, body=html_msg, subject=title
                )
            for partner in target_partners:
                self.env['bus.bus']._sendone(
                    partner, 'simple_notification',
                    {'type': 'warning', 'title': title, 'message': plain_msg, 'sticky': True}
                )

    def _notify_production_users_so_confirmed(self):
        self._notify_group_so_confirmed('fk_kalsal_security.group_executor_production')

    def _notify_procurement_users_so_confirmed(self):
        self._notify_group_so_confirmed('fk_kalsal_security.group_executor_procurement')

    def action_confirm(self):
        res = super(SalesOrder, self).action_confirm()

        for line in self.order_line:
            if line.product_id and not line.product_id.bom_ids:
                raise UserError(
                    _('Bill Of Material for Product %s is not available. Manufacturing Order will not be created') % line.product_id.name)

        self._notify_procurement_users_so_confirmed()

        return res


class SalesOrderLine(models.Model):
    _inherit = 'sale.order.line'

    remark = fields.Char(string='Remark', store=True)
    s_no = fields.Integer(string='S No#', compute='_compute_s_no', store=False)

    product_id = fields.Many2one(
        domain="[('product_type_custom', '=', 'finished')]"
    )

    @api.depends('order_id.order_line')
    def _compute_s_no(self):
        for order in self.mapped('order_id'):
            for index, line in enumerate(order.order_line, start=1):
                line.s_no = index