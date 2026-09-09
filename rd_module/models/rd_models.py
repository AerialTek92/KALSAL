from odoo import models, fields, api, _
from odoo.exceptions import UserError


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    # 1. Updated Selection options to include the new 'rejected' state tracking phase
    state = fields.Selection([
        ('draft', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected')
    ], string='Status', default='draft', tracking=True, copy=False)

    # group_operator="max" ensures the header row displays the latest version number
    version = fields.Float(string='Version', default=1.0, tracking=True, copy=False, readonly=True,
                           group_operator="max")

    # Added default=fields.Datetime.now so it stamps immediately upon creation/cloning
    version_date = fields.Datetime(
        string='Version Created Date',
        default=fields.Datetime.now,
        readonly=True,
        copy=False,
        tracking=True
    )

    def action_approve_bom(self):
        """Action method to approve the current BoM. It simply locks it as active."""
        self.ensure_one()
        if self.state == 'draft':
            self.write({'state': 'approved'})
            self.message_post(body=_("Recipe variation authorized. Version %s is now active.") % self.version)
        else:
            self.state = 'approved'

    # NEW ACTION METHOD: Changes status to Rejected
    def action_reject_bom(self):
        self.ensure_one()
        if self.state == 'draft':
            self.write({'state': 'rejected'})
            self.message_post(body=_("Recipe variation has been explicitly rejected by the Approver."))

    def action_reset_to_draft(self):
        self.ensure_one()
        self.state = 'draft'

    def unlink(self):
        raise UserError(_("Deletion of Bill of Materials is strictly prohibited. Please archive it instead."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['state'] = 'draft'
            if 'version' not in vals:
                vals['version'] = 1.0
            if 'version_date' not in vals:
                vals['version_date'] = fields.Datetime.now()
        return super(MrpBom, self).create(vals_list)

    def write(self, vals):
        # SANDBOX RULE: If someone edits an already APPROVED recipe, block the edit on the original.
        # Instead, clone it into a brand-new row and apply the changes there!
        if self.state == 'approved' and 'state' not in vals and 'version' not in vals:
            # Prevent infinite processing loops while cloning
            if not self.env.context.get('cloning_bom_sandbox'):
                for bom in self:
                    next_version = bom.version + 1.0
                    base_ref = bom.code or bom.product_tmpl_id.name

                    # ULTIMATE TEXT CLEANING LOGIC:
                    # Grabbing [0] guarantees we extract only the clean string name text
                    # and completely drops any messy legacy bracket/list structures!
                    if " V" in str(base_ref):
                        base_ref = str(base_ref).split(" V")[0]

                    # If an old record contains brackets, clean them out entirely
                    if "[" in str(base_ref):
                        base_ref = str(base_ref).replace("[", "").replace("]", "").replace("'", "").split(",")[0]

                    new_ref = f"{base_ref.strip()} V{int(next_version)}"

                    # 1. Spawn a clean copy of the entire parent BoM and its line items
                    new_bom = bom.with_context(cloning_bom_sandbox=True).copy({
                        'state': 'draft',
                        'version': next_version,
                        'code': new_ref,
                        'version_date': fields.Datetime.now()
                    })

                    # ==========================================
                    # FIX: TRANSLATE LINE IDS FOR ACCURATE CLONING
                    # ==========================================
                    line_commands = vals.get('bom_line_ids')
                    if line_commands:
                        # Map old line IDs to new line IDs using product_id (which is unique per BOM line)
                        id_map = {}
                        for new_line in new_bom.bom_line_ids:
                            old_line = bom.bom_line_ids.filtered(lambda l: l.product_id == new_line.product_id)
                            if old_line:
                                id_map[old_line.id] = new_line.id

                        translated_commands = []
                        for cmd in line_commands:
                            if cmd[0] == 0:  # CREATE
                                translated_commands.append(cmd)
                            elif cmd[0] == 1:  # UPDATE
                                new_id = id_map.get(cmd[1])
                                if new_id:
                                    translated_commands.append((1, new_id, cmd[2]))
                            elif cmd[0] == 2:  # DELETE
                                new_id = id_map.get(cmd[1])
                                if new_id:
                                    translated_commands.append((2, new_id, False))
                            elif cmd[0] == 4:  # LINK
                                translated_commands.append(cmd)
                            elif cmd[0] == 5:  # CLEAR ALL
                                translated_commands.append(cmd)
                            elif cmd[0] == 6:  # REPLACE ALL
                                # Fallback to clearing all lines if this rare command is triggered
                                translated_commands.append((5, 0, 0))

                        vals['bom_line_ids'] = translated_commands

                    # 2. Write the user's modifications ONLY into this new duplicate row
                    super(MrpBom, new_bom).write(vals)

                    # 3. Post a clean audit note to the original record's log for the tracking team
                    bom.message_post(
                        body=_(
                            "Modification intercepted. Original recipe preserved. New sandbox draft generated: %s (Version %s)") % (
                                 new_ref, next_version)
                    )

                # Intercept the original write action so the old row remains completely pristine!
                return True

        return super(MrpBom, self).write(vals)


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    def unlink(self):
        """Intercept direct deletion of lines from an Approved BoM to trigger sandboxing."""
        if not self.env.context.get('cloning_bom_sandbox'):
            for line in self:
                if line.bom_id.state == 'approved':
                    # Force through parent write to trigger the sandbox clone logic
                    line.bom_id.write({'bom_line_ids': [(2, line.id, False)]})
                    return  # Prevent actual deletion from the approved BOM
        return super(MrpBomLine, self).unlink()

    def write(self, vals):
        if not self.env.context.get('cloning_bom_sandbox'):
            for line in self:
                if line.bom_id.state == 'approved':
                    return line.bom_id.write({'bom_line_ids': [(1, line.id, vals)]})
        return super(MrpBomLine, self).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get('cloning_bom_sandbox'):
            for vals in vals_list:
                if vals.get('bom_id'):
                    bom = self.env['mrp.bom'].browse(vals['bom_id'])
                    if bom.state == 'approved':
                        bom.write({'bom_line_ids': [(0, 0, vals)]})
                        return self.env['mrp.bom.line']
        return super(MrpBomLine, self).create(vals_list)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    bom_id = fields.Many2one(
        'mrp.bom',
        string='Recipe Version',
        help="Choose the approved recipe version for this item"
    )

    product_tmpl_id = fields.Many2one(
        'product.template',
        related='product_id.product_tmpl_id',
        string='Product Template Helper',
        store=False
    )

    @api.onchange('product_id')
    def _onchange_product_id_set_default_bom(self):
        if self.product_id:
            matching_bom = self.env['mrp.bom'].search([
                ('product_tmpl_id', '=', self.product_id.product_tmpl_id.id),
                ('state', '=', 'approved')
            ], limit=1, order='id desc')

            if matching_bom:
                self.bom_id = matching_bom.id
            else:
                self.bom_id = False


class StockRule(models.Model):
    _inherit = 'stock.rule'

    def _prepare_mo_vals(self, *args, **kwargs):
        res = super(StockRule, self)._prepare_mo_vals(*args, **kwargs)

        values = kwargs.get('values')
        if values is None and len(args) > 7:
            values = args[7]

        if isinstance(values, dict) and values.get('sale_line_id'):
            sale_line = self.env['sale.order.line'].browse(values['sale_line_id'])

            if sale_line.bom_id:
                res['bom_id'] = sale_line.bom_id.id

        return res
