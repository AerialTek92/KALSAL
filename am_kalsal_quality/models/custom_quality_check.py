from datetime import datetime, timedelta
from odoo import models, fields, api, _, Command
from odoo.exceptions import UserError
import re


class KalsalQualityCheck(models.Model):
    _name = 'kalsal.quality.check'
    _description = 'Custom Quality Check'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    # ==================== EXISTING FIELDS ====================
    name = fields.Char(string='Reference', default='New', readonly=True, tracking=True)
    date = fields.Datetime(string='Date', default=fields.Datetime.now, tracking=True)
    product_id = fields.Many2one('product.product', string='Product', tracking=True)
    picking_ids = fields.Many2one('stock.picking', string='Linked Picking')
    stock_up_picking = fields.Many2one('stock.picking', string='Stock Up Picking')

    partner_id = fields.Many2one('res.partner', string='Supplier/Customer')
    lot_id = fields.Many2one('stock.lot', string='Lot/Serial')
    responsible_id = fields.Many2one('res.users', string='Responsible', default=lambda self: self.env.user)
    notes = fields.Html(string='Notes')
    color_id = fields.Many2one('color.parameter', string='Color')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('1st_in_progress', '1st QC In Progress'),
        ('waiting_delay', '1st QC Passed'),
        ('2nd_in_progress', '2nd QC In Progress'),
        ('pass', 'Passed'),
        ('fail', 'Failed'),
        ('cancel', 'Cancelled'),
    ], string='State', default='draft', tracking=True, group_expand='_group_expand_states')

    formulated_ingredients = fields.Char(string='Formulated Ingredients')
    packing_n_delivery = fields.Char(string='Packing & Delivery')

    check_line_ids = fields.One2many('kalsal.quality.check.line', 'check_id', string='Test Parameters')
    total_pass = fields.Integer(string='Passed', compute='_compute_check_summary', store=True)
    total_fail = fields.Integer(string='Failed', compute='_compute_check_summary', store=True)
    total_pending = fields.Integer(string='Pending', compute='_compute_check_summary', store=True)
    overall_result = fields.Selection([
        ('pass', 'Pass'), ('fail', 'Fail'), ('pending', 'Pending'),
    ], string='Overall Result', compute='_compute_check_summary', store=True, tracking=True)

    has_delayed_params = fields.Boolean(string='Has Delayed Parameters', compute='_compute_has_delayed_params',
                                        store=True)
    delay_start_date = fields.Datetime(string='Delay Start Date', tracking=True, readonly=True)
    delay_due_date = fields.Datetime(string='Delay Due Date', compute='_compute_delay_due_date', store=True,
                                     readonly=False)
    delay_days = fields.Integer(string='Delay Period (Days)', default=8, tracking=True,
                                help="Number of days to wait after 1st QC before 2nd QC results can be entered for delayed parameters.")
    is_delay_overdue = fields.Boolean(string='Delay Overdue', compute='_compute_is_delay_overdue',
                                      search='_search_is_delay_overdue')
    delay_notification_sent = fields.Boolean(string='Delay Notification Sent', default=False)
    first_qc_date = fields.Datetime(string='1st QC Completion Date', readonly=True)
    second_qc_date = fields.Datetime(string='2nd QC Completion Date', readonly=True)
    days_remaining = fields.Integer(string='Days Remaining', compute='_compute_days_remaining')
    kanban_state = fields.Selection([
        ('normal', 'Normal'), ('done', 'Ready'), ('blocked', 'Blocked'),
    ], string='Kanban State', default='normal', compute='_compute_kanban_state')

    product_tmpl_id = fields.Many2one(
        'product.template',
        related='product_id.product_tmpl_id',
        string='Product Template',
        store=True,
        readonly=True
    )

    received_qty = fields.Float(string='Received Quantity', compute='_compute_received_qty', store=True, readonly=False,
                                tracking=True)
    accepted_qty = fields.Float(string='Accepted Quantity', tracking=True)
    rejected_qty = fields.Float(string='Rejected Quantity', compute='_compute_rejected_qty', store=True, readonly=False,
                                tracking=True)
    return_picking_id = fields.Many2one('stock.picking', string='Return Picking', readonly=True)
    is_partial = fields.Boolean(string='Is Partial Acceptance', compute='_compute_is_partial', store=True)

    # 2. NEW: Shows ONLY lines where Delay is NOT required
    first_qc_line_ids = fields.One2many(
        'kalsal.quality.check.line',
        'check_id',
        string='1st QC Parameters',
        domain=[('delay_required', '=', False)]
    )

    # 3. NEW: Shows ONLY lines where Delay IS required
    second_qc_line_ids = fields.One2many(
        'kalsal.quality.check.line',
        'check_id',
        string='2nd QC Parameters',
        domain=[('delay_required', '=', True)]
    )
    # ==================== COMPUTE METHODS ====================

    @api.depends('picking_ids', 'picking_ids.move_ids', 'picking_ids.move_ids.quantity', 'product_id')
    def _compute_received_qty(self):
        for rec in self:
            total = 0.0
            if rec.picking_ids and rec.product_id:
                moves = rec.picking_ids.move_ids.filtered(lambda m: m.product_id.id == rec.product_id.id)
                total = sum(m.quantity for m in moves)
            rec.received_qty = total
            if not rec.accepted_qty:
                rec.accepted_qty = total

    @api.depends('received_qty', 'accepted_qty')
    def _compute_rejected_qty(self):
        for rec in self:
            rec.rejected_qty = max(0.0,
                                   rec.received_qty - rec.accepted_qty) if rec.received_qty and rec.accepted_qty else 0.0

    @api.depends('accepted_qty', 'rejected_qty', 'received_qty')
    def _compute_is_partial(self):
        for rec in self:
            rec.is_partial = rec.received_qty > 0 and rec.accepted_qty > 0 and rec.accepted_qty < rec.received_qty

    @api.onchange('accepted_qty')
    def _onchange_accepted_qty(self):
        if self.accepted_qty and self.received_qty:
            if self.accepted_qty > self.received_qty:
                raise UserError(_("Accepted quantity (%s) cannot exceed received quantity (%s).") % (self.accepted_qty,
                                                                                                     self.received_qty))
            if self.accepted_qty < 0:
                raise UserError(_("Accepted quantity cannot be negative."))

    @api.depends('check_line_ids', 'check_line_ids.delay_required')
    def _compute_has_delayed_params(self):
        for rec in self:
            rec.has_delayed_params = bool(rec.check_line_ids.filtered('delay_required'))

    @api.depends('delay_start_date', 'delay_days')
    def _compute_delay_due_date(self):
        for rec in self:
            rec.delay_due_date = rec.delay_start_date + timedelta(
                days=rec.delay_days) if rec.delay_start_date and rec.delay_days else False

    @api.depends('delay_due_date', 'state')
    def _compute_is_delay_overdue(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_delay_overdue = rec.state == 'waiting_delay' and rec.delay_due_date and rec.delay_due_date <= now

    def _search_is_delay_overdue(self, operator, value):
        if isinstance(value, str):
            val = value.lower() in ('true', '1', 't', 'yes')
        elif isinstance(value, list):
            val = any(v in (True, 'true', 'True', 1, 't', 'yes') for v in value)
        else:
            val = bool(value)

        if operator in ('!=', 'not in'):
            val = not val

        if val:
            return [('state', '=', 'waiting_delay'), ('delay_due_date', '<', fields.Datetime.now())]
        return ['|', ('state', '!=', 'waiting_delay'), ('delay_due_date', '>=', fields.Datetime.now())]

    @api.depends('delay_due_date', 'state')
    def _compute_days_remaining(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.days_remaining = max(0, (
                    rec.delay_due_date - now).days) if rec.delay_due_date and rec.state == 'waiting_delay' else 0

    @api.depends('state', 'delay_due_date')
    def _compute_kanban_state(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.state == 'pass':
                rec.kanban_state = 'done'
            elif rec.state == 'waiting_delay' and rec.delay_due_date and rec.delay_due_date < now:
                rec.kanban_state = 'blocked'
            else:
                rec.kanban_state = 'normal'

    @api.depends('check_line_ids.status')
    def _compute_check_summary(self):
        for rec in self:
            lines = rec.check_line_ids
            rec.total_pass = len(lines.filtered(lambda l: l.status == 'pass'))
            rec.total_fail = len(lines.filtered(lambda l: l.status == 'fail'))
            rec.total_pending = len(lines.filtered(lambda l: l.status in ('pending', 'na') or not l.status))
            if rec.total_fail > 0:
                rec.overall_result = 'fail'
            elif rec.total_pending > 0:
                rec.overall_result = 'pending'
            elif rec.total_pass > 0:
                rec.overall_result = 'pass'
            else:
                rec.overall_result = 'pending'

    @api.model
    def _group_expand_states(self, states, domain, order):
        return [key for key, _ in self._fields['state'].selection]

    # ==================== HELPER METHODS ====================

    def _get_picking_action(self, picking_id, name='Stock Picking'):
        return {
            'name': _(name),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking_id,
            'context': self.env.context,
        }

    def _get_activity_type(self, xml_id):
        # We remove the fallback so Odoo screams at us if the XML ID is wrong
        # instead of silently making generic To-Dos.
        return self.env.ref(xml_id, raise_if_not_found=True)

    def _send_browser_notification(self, title, message, sticky=False, warning=True):
        if self.responsible_id and self.responsible_id.partner_id:
            action = self.env.ref('am_kalsal_quality.action_delayed_qc_list', raise_if_not_found=False)
            url = f"/odoo/action-{action.id}" if action else None
            notification_vals = {'title': title, 'message': message, 'sticky': sticky, 'warning': warning}
            if url: notification_vals['url'] = url
            self.env['bus.bus']._sendone(self.responsible_id.partner_id, 'simple_notification', notification_vals)

    # ==================== ACTION METHODS ====================

    def action_open_partial_wizard(self):
        self.ensure_one()
        for rec in self:
            if not rec.check_line_ids:
                raise UserError(
                    _("No test parameters found. Please load test parameters before completing the 1st QC."))
            non_delayed_lines = rec.check_line_ids.filtered(lambda l: not l.delay_required)
            if self.state == '1st_in_progress':
                self._validate_qc_lines(non_delayed_lines, "1st QC", "Partial")
            elif self.state == '2nd_in_progress':
                rec._validate_qc_lines(rec.check_line_ids.filtered('delay_required'), "2nd QC", "Partial")

        incoming_picking = self.picking_ids
        if not incoming_picking:
            raise UserError(_("No linked picking found."))

        lines_vals = []
        move_lines = incoming_picking.move_line_ids.filtered(
            lambda ml: ml.product_id.id == self.product_id.id and ml.quantity > 0)

        if move_lines:
            for ml in move_lines:
                lines_vals.append((0, 0, {'move_line_id': ml.id, 'product_id': ml.product_id.id,
                                          'lot_id': ml.lot_id.id if ml.lot_id else False, 'total_qty': ml.quantity,
                                          'accepted_qty': 0.0}))
        else:
            moves = incoming_picking.move_ids.filtered(lambda m: m.product_id.id == self.product_id.id)
            if not moves:
                raise UserError(
                    _("No stock moves with quantity > 0 found for this product. Please ensure the received quantity is set on the receipt."))
            for move in moves:
                lines_vals.append((0, 0, {'move_line_id': False, 'product_id': move.product_id.id, 'lot_id': False,
                                          'total_qty': move.quantity if move.quantity else move.product_uom_qty,
                                          'accepted_qty': 0.0}))

        wizard = self.env['kalsal.partial.acceptance.wizard'].create({'qc_id': self.id, 'line_ids': lines_vals})
        return {'name': _('Partial Acceptance'), 'type': 'ir.actions.act_window',
                'res_model': 'kalsal.partial.acceptance.wizard', 'res_id': wizard.id, 'view_mode': 'form',
                'target': 'new', 'context': self.env.context}

    def process_lot_based_partial_acceptance(self, lot_data):
        self.ensure_one()
        if not self.picking_ids: raise UserError(_("No linked picking found."))
        accepted_total = sum(d['accepted_qty'] for d in lot_data)
        rejected_total = sum(d['rejected_qty'] for d in lot_data)

        self.write({'accepted_qty': accepted_total, 'rejected_qty': rejected_total, 'state': 'pass'})
        self.compute_picking_state({})
        self.message_post(
            body=_("<b>Quality Check PASSED with Partial Acceptance.</b><br/>Accepted: %s<br/>Rejected: %s") % (
                accepted_total, rejected_total))

        for line in self.picking_ids.move_ids:
            if line.product_id.id == self.product_id.id:
                line.write({'quantity': accepted_total, 'rejected_qty': rejected_total, 'accepted_qty': accepted_total,
                            'qc_updated': True})

    def _product_back_to_pr(self):
        for rec in self:
            if not rec.picking_ids:
                rec.message_post(body=_("No linked picking found. Auto-return skipped."))
                continue
            failed_moves = rec.picking_ids.move_ids.filtered(lambda m: m.product_id.id == rec.product_id.id)
            if not failed_moves: continue
            for move in failed_moves:
                move.quantity = 0
                for ml in move.move_line_ids: ml.quantity = 0
                if move.purchase_line_id:
                    move.purchase_line_id.qc_failed = True
                    move.purchase_line_id.order_id._button_redo()
            rec.compute_picking_state(failed_moves)
            rec.message_post(body=_(
                "<b>QC Failed - Goods Rejected.</b><br/>Received quantity on GRN set to 0. Stock will NOT be moved to WH/Stock."))

    def _partial_return_rejected_goods(self):
        self.ensure_one()
        if self.rejected_qty <= 0: return False
        if not self.picking_ids:
            self.message_post(body=_("No linked picking found. Partial return skipped."))
            return False

        source_move = self.picking_ids.move_ids.filtered(lambda m: m.product_id.id == self.product_id.id)
        if not source_move:
            self.message_post(
                body=_("<b>Partial Return Skipped:</b> No stock move found for this product on the Receipt."))
            return False

        source_move = source_move[0]
        if self.accepted_qty <= 0:
            source_move.quantity = 0
            for ml in source_move.move_line_ids: ml.quantity = 0
            self.message_post(body=_(
                "<b>Partial Return - All Goods Rejected.</b><br/>Received quantity on GRN set to 0 for product %s.") % self.product_id.name)
        else:
            source_move.quantity = self.accepted_qty
            for ml in source_move.move_line_ids: ml.quantity = self.accepted_qty
            self.message_post(body=_(
                "<b>Partial Acceptance Applied to GRN.</b><br/>Received quantity reduced to: %s %s.<br/>Rejected Quantity: %s %s will not be received.") % (
                                       self.accepted_qty, self.product_id.uom_id.name, self.rejected_qty,
                                       self.product_id.uom_id.name))
        return True

    def compute_picking_state(self, failed_moves):
        incoming_picking = self.picking_ids
        if not incoming_picking or incoming_picking.state == 'done': return

        total_products = len(incoming_picking.move_ids.mapped('product_id'))
        failed_count = self.env['kalsal.quality.check'].search_count(
            [('picking_ids', '=', incoming_picking.id), ('state', '=', 'fail')])
        completed_qcs = self.env['kalsal.quality.check'].search_count(
            [('picking_ids', '=', incoming_picking.id), ('state', 'in', ('pass', 'fail', 'waiting_delay', 'cancel'))])

        if failed_count == total_products:
            incoming_picking.write({'state': 'qc_failed'})
        elif completed_qcs == total_products:
            incoming_picking.write({'state': 'assigned'})
        else:
            incoming_picking.write({'state': 'qc_pending'})

    def action_view_qc_picking(self):
        self.ensure_one()
        return self._get_picking_action(self.picking_ids.id)

    def action_start(self):
        self.ensure_one()
        self._load_default_test_lines()
        self.state = '1st_in_progress'

    def action_complete_1st_qc(self):
        for rec in self:
            if not rec.check_line_ids:
                raise UserError(
                    _("No test parameters found. Please load test parameters before completing the 1st QC."))
            rec._validate_qc_lines(rec.check_line_ids.filtered(lambda l: not l.delay_required), "1st QC", "Pass")
            rec.first_qc_date = fields.Datetime.now()

            if rec.has_delayed_params:
                rec.state = 'waiting_delay'
                rec.delay_start_date = fields.Datetime.now()
                delayed_names = ', '.join(rec.check_line_ids.filtered('delay_required').mapped('test_parameter'))
                rec.message_post(body=_(
                    "<b>1st QC Completed.</b><br/>Waiting %s day(s) for delayed parameter results.<br/>Delayed parameters: %s<br/>2nd QC due date: %s") % (
                                          rec.delay_days, delayed_names, rec.delay_due_date.strftime(
                                          '%Y-%m-%d %H:%M') if rec.delay_due_date else 'N/A'))
            else:
                rec.state = 'pass'
                if rec.rejected_qty > 0: rec._partial_return_rejected_goods()
                rec.message_post(
                    body=_("<b>1st QC Completed – Quality Check PASSED.</b> GRN is now ready for manual validation."))

            rec.compute_picking_state({})
            if rec.picking_ids:
                return rec._get_picking_action(rec.picking_ids.id)

    def action_start_2nd_qc(self):
        for rec in self:
            rec.state = '2nd_in_progress'
            rec.message_post(body=_("<b>2nd QC Started.</b><br/>Please enter results for the delayed test parameters."))

    def _validate_qc_lines(self, lines, phase_label, result):
        if not lines: return
        incomplete_lines = lines.filtered(lambda l: l.status == 'pending' or (not l.result and l.status != 'na'))
        if incomplete_lines:
            raise UserError(
                _("%s Validation Error:\n\nThe following test parameters are missing either a result or a status:\n\n• %s") % (
                    phase_label, '\n• '.join(incomplete_lines.mapped('test_parameter'))))
        failed_no_remarks = lines.filtered(lambda l: l.status == 'fail' and not l.remarks and result == "Pass")
        if failed_no_remarks:
            raise UserError(
                _("%s Validation Error:\n\nThe following parameters are marked as FAILED but have no remarks:\n\n• %s") % (
                    phase_label, '\n• '.join(failed_no_remarks.mapped('test_parameter'))))

    def action_pass(self):
        for rec in self:
            if rec.received_qty > 0 and (rec.accepted_qty < 0 or rec.accepted_qty > rec.received_qty):
                raise UserError(_("Accepted quantity must be between 0 and %s.") % rec.received_qty)

            if rec.state == '2nd_in_progress':
                rec._validate_qc_lines(rec.check_line_ids.filtered('delay_required'), "2nd QC", "Pass")
                rec.second_qc_date = fields.Datetime.now()
                rec.state = 'pass'
                if rec.rejected_qty > 0: rec._partial_return_rejected_goods()
                rec.message_post(
                    body=_("<b>2nd QC Completed – Quality Check PASSED.</b> GRN is now ready for manual validation."))
            elif rec.state == '1st_in_progress':
                rec.action_complete_1st_qc()
            elif rec.state == 'waiting_delay':
                rec._validate_qc_lines(rec.check_line_ids.filtered('delay_required'), "2nd QC (Bypassed Delay)", "Pass")
                rec.second_qc_date = fields.Datetime.now()
                rec.state = 'pass'
                if rec.rejected_qty > 0: rec._partial_return_rejected_goods()
                rec.message_post(body=_(
                    "<b>Quality Check PASSED</b> (delay period bypassed). GRN is now ready for manual validation."))
            rec.compute_picking_state({})

    def action_fail(self):
        for rec in self:
            if not rec.check_line_ids:
                raise UserError(
                    _("No test parameters found. Please load test parameters before completing the 1st QC."))
            if rec.state == '1st_in_progress' and not rec.first_qc_date:
                rec._validate_qc_lines(rec.check_line_ids.filtered(lambda l: not l.delay_required), "1st QC", "Fail")
                rec.first_qc_date = fields.Datetime.now()
            elif rec.state == '2nd_in_progress' and not rec.second_qc_date:
                rec._validate_qc_lines(rec.check_line_ids.filtered('delay_required'), "2nd QC", "Fail")
                rec.second_qc_date = fields.Datetime.now()

            rec.write({'accepted_qty': 0.0, 'rejected_qty': rec.received_qty, 'state': 'fail'})
            rec.message_post(
                body=_("<b>Quality Check FAILED.</b><br/>Full quantity (%s %s) will be returned to vendor.") % (
                    rec.received_qty, rec.product_id.uom_id.name))

            for line in rec.picking_ids.move_ids:
                if line.product_id.id == rec.product_id.id:
                    line.write({'product_uom_qty': 0, 'quantity': 0})
            rec._product_back_to_pr()

    def action_view_return_picking(self):
        self.ensure_one()
        if not self.return_picking_id: raise UserError(_("No return picking has been generated yet."))
        return self._get_picking_action(self.return_picking_id.id, name='Return Picking')

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancel'
            rec.message_post(body=_("<b>Quality Check Cancelled.</b>"))

    def action_load_default_lines(self):
        self._load_default_test_lines()

    # ==================== CRON: DELAY NOTIFICATION ====================

    def _cron_check_delay_overdue(self):
        waiting_records = self.search([('state', '=', 'waiting_delay')])
        waiting_records._compute_is_delay_overdue()
        waiting_records.flush_recordset(['is_delay_overdue'])

        overdue_records = self.search([('state', '=', 'waiting_delay'), ('delay_due_date', '<', fields.Datetime.now())])
        for rec in overdue_records:
            rec._send_delay_notification()

        approaching = self.search([
            ('state', '=', 'waiting_delay'),
            ('delay_notification_sent', '=', False),
            ('delay_due_date', '>=', fields.Datetime.now()),
            ('delay_due_date', '<=', fields.Datetime.now() + timedelta(days=1)),
        ])
        for rec in approaching:
            rec._send_delay_reminder()

    def _send_delay_notification(self):
        self.ensure_one()
        activity_type = self._get_activity_type('am_kalsal_quality.mail_activity_type_2nd_qc')

        if activity_type and self.responsible_id:
            existing_activity_count = self.env['mail.activity'].search_count([
                ('res_id', '=', self.id),
                ('res_model', '=', self._name),
                ('activity_type_id', '=', activity_type.id),
                ('user_id', '=', self.responsible_id.id),
            ])
            if not existing_activity_count:
                self.activity_schedule(
                    activity_type_id=activity_type.id,
                    user_id=self.responsible_id.id,
                    date_deadline=self.delay_due_date,  # <--- ADD THIS to make it show as Late
                    summary=_('Delay Period Ended - 2nd QC Required'),
                    note=_(
                        "The %s-day delay period for Quality Check %s has ended.\n\nDelayed parameters requiring results:\n%s\n\nPlease proceed with the 2nd QC to complete these tests.") % (
                             self.delay_days, self.name, '\n'.join('• ' + p for p in self.check_line_ids.filtered(
                             'delay_required').mapped('test_parameter'))),
                )

        if not self.delay_notification_sent:
            reminder_activity_type = self._get_activity_type('am_kalsal_quality.mail_activity_type_delay_reminder')
            if reminder_activity_type:
                activities_to_delete = self.env['mail.activity'].search(
                    [('res_id', '=', self.id), ('res_model', '=', self._name),
                     ('activity_type_id', '=', reminder_activity_type.id)])
                if activities_to_delete: activities_to_delete.sudo().unlink()

            self.message_post(
                body=_(
                    "<b>⚠️ Delay Period Ended</b><br/><br/>The %s-day delay period has ended. Please proceed with the 2nd QC to enter results for the delayed test parameters:<br/>%s") % (
                         self.delay_days, '<br/>'.join(
                         '• ' + p for p in self.check_line_ids.filtered('delay_required').mapped('test_parameter'))),
                subject=_("Delay Period Ended - Action Required"),
            )
            self.delay_notification_sent = True

        self._send_browser_notification(
            title=_('Quality Check Delay Ended'),
            message=_('The delay period for %s has ended. Please complete the 2nd QC.') % self.name,
            sticky=False
        )

    def _send_delay_reminder(self):
        self.ensure_one()
        activity_type = self._get_activity_type('am_kalsal_quality.mail_activity_type_delay_reminder')

        if activity_type and self.responsible_id:
            existing_activity_count = self.env['mail.activity'].search_count([
                ('res_id', '=', self.id),
                ('res_model', '=', self._name),
                ('activity_type_id', '=', activity_type.id),
                ('user_id', '=', self.responsible_id.id),
            ])
            if not existing_activity_count:
                self.activity_schedule(
                    activity_type_id=activity_type.id,
                    user_id=self.responsible_id.id,
                    date_deadline=self.delay_due_date,
                    summary=_('Delay Period Ending Soon'),
                    note=_(
                        "The delay period for Quality Check %s will end on %s.\n\nPlease prepare to enter results for the delayed parameters.") % (
                             self.name,
                             self.delay_due_date.strftime('%Y-%m-%d %H:%M') if self.delay_due_date else 'N/A'),
                )

        self._send_browser_notification(
            title=_('Quality Check Delay Ending Soon'),
            message=_('The delay period for %s will end on %s. Please prepare to enter results.') % (self.name,
                                                                                                     self.delay_due_date.strftime(
                                                                                                         '%Y-%m-%d %H:%M') if self.delay_due_date else 'N/A')
        )

    @api.onchange('product_id')
    def _onchange_product_id_clear_color(self):
        """ Clears the color selection if it doesn't match the new product's template """
        if self.color_id and self.product_id:
            if self.color_id.product_tmpl_id != self.product_id.product_tmpl_id:
                self.color_id = False

    # ==================== CRUD ====================

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('kalsal.quality.check') or 'New'
        records = super().create(vals_list)
        records._load_default_test_lines()
        return records

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.check_line_ids = [(5, 0, 0)]
            self._load_default_test_lines()
            if not self.check_line_ids:
                raise UserError(
                    _("Product %s does not contains specification, Kindly go to Product Page and add parameters for Passing Quality Check."))
        else:
            raise UserError(_("Product ID must be provided in order to start Quality Check."))

    def _load_default_test_lines(self):
        for rec in self:
            if rec.check_line_ids: continue
            if rec.product_id and rec.product_id.product_tmpl_id.quality_param_line_ids:
                lines_to_create = []
                seq = 10
                for param_line in rec.product_id.product_tmpl_id.quality_param_line_ids:
                    lines_to_create.append((0, 0, {
                        'sequence': seq,
                        'test_parameter': param_line.parameter_id.name,
                        'condition': param_line.condition or param_line.parameter_id.default_condition,
                        'specification': param_line.specification or param_line.parameter_id.default_specification,
                        'delay_required': param_line.delay_required,
                        'status': 'pending',
                    }))
                    seq += 10
                if lines_to_create:
                    rec.check_line_ids = lines_to_create


class KalsalQualityCheckLine(models.Model):
    _name = 'kalsal.quality.check.line'
    _description = 'Quality Check Test Line'
    _order = 'sequence, id'

    check_id = fields.Many2one('kalsal.quality.check', string='Quality Check', ondelete='cascade', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    test_parameter = fields.Char(string='Test Parameter', required=True)
    condition = fields.Selection([('nmt', 'Not More Than'), ('nlt', 'Not Less Than')])
    specification = fields.Char(string='Specification / Limit')
    result = fields.Char(string='Result')
    status = fields.Selection([('pass', 'Pass'), ('fail', 'Fail'), ('na', 'N/A'), ('pending', 'Pending')],
                              string='Status', default='pending', compute='_compute_status', store=True, readonly=False)
    remarks = fields.Char(string='Remarks')
    status_color = fields.Integer(string='Status Color', compute='_compute_status_color')
    delay_required = fields.Boolean(string='Delay Required', default=False)

    is_visible = fields.Boolean(
        string='Visible',
        compute='_compute_is_visible',
        store=True
    )

    @api.depends('check_id.state', 'delay_required')
    def _compute_is_visible(self):
        for rec in self:
            if not rec.check_id:
                rec.is_visible = True
                continue

            state = rec.check_id.state

            if state == '1st_in_progress':
                # 1st QC Started: Show ONLY parameters where delay is NOT required
                rec.is_visible = not rec.delay_required

            elif state in ('waiting_delay', '2nd_in_progress'):
                # 1st QC Passed (waiting_delay) & 2nd QC Started: Show ONLY Delay Required parameters
                rec.is_visible = rec.delay_required

            else:
                # Done/Passed, Failed, Draft, Cancel: Show ALL parameters
                rec.is_visible = True

    def _search_is_visible(self, operator, value):
        # Dummy search to bypass XML domain validation.
        # The UI will handle the actual filtering dynamically.
        return [(1, '=', 1)]

    def _parse_to_float(self, value_str):
        if not value_str: return None
        match = re.search(r'[-+]?(?:\d*\.\d+|\d+)', str(value_str).strip())
        try:
            return float(match.group()) if match else None
        except ValueError:
            return None

    @api.depends('result', 'specification', 'condition')
    def _compute_status(self):
        for rec in self:
            if not rec.result:
                if rec.status not in ['na']: rec.status = 'pending'
                continue
            target_val = rec._parse_to_float(rec.specification)
            actual_val = rec._parse_to_float(rec.result)
            if target_val is None or actual_val is None: continue
            if rec.condition == 'nmt':
                rec.status = 'pass' if actual_val <= target_val else 'fail'
            elif rec.condition == 'nlt':
                rec.status = 'pass' if actual_val >= target_val else 'fail'

    @api.depends('status')
    def _compute_status_color(self):
        color_map = {'pass': 10, 'fail': 1, 'na': 4, 'pending': 3}
        for rec in self:
            rec.status_color = color_map.get(rec.status, 0)