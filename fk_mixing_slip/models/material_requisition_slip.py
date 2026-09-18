from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, float_round

class MaterialRequisitionSlip(models.Model):
    _inherit = 'material.requisition.slip'

    mixing_slip_ids = fields.One2many(
        'mixing.slip', 'mrs_id', string='Mixing Slips')

    mixing_slip_count = fields.Integer(
        string='Mixing Slip Count', compute='_compute_mixing_slip_count')

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

    # ---------- NEW: mixing-gate helper ----------
    def _product_has_pending_mixing(self, product, sale_order, exclude_id=None):
        """True if a non-cancelled MRS already exists for this product/SO
        whose Mixing hasn't been completed yet — either no Mixing Slip
        was ever created for it, or one exists but isn't 'done'."""
        domain = [
            ('sale_order_id', '=', sale_order.id),
            ('recipe_product_id', '=', product.id),
            ('state', 'in', ('confirmed', 'done')),
        ]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        prior_mrs = self.env['material.requisition.slip'].search(domain)
        for mrs in prior_mrs:
            if not mrs.mixing_slip_ids or any(s.state != 'done' for s in mrs.mixing_slip_ids):
                return True
        return False

    # ---------- Hide products with pending mixing from the picker ----------
    @api.depends('sale_order_id')
    def _compute_allowed_recipe_product_ids(self):
        super()._compute_allowed_recipe_product_ids()
        for rec in self:
            if not rec.sale_order_id or not rec.allowed_recipe_product_ids:
                continue
            blocked = self.env['product.product']
            for product in rec.allowed_recipe_product_ids:
                if rec._product_has_pending_mixing(product, rec.sale_order_id, exclude_id=rec.id):
                    blocked |= product
            rec.allowed_recipe_product_ids -= blocked

    # ---------- Onchange: block selection with a clear message ----------
    @api.onchange('recipe_product_id')
    def _onchange_recipe_product_id(self):
        res = super()._onchange_recipe_product_id()
        if self.recipe_product_id and self.sale_order_id:
            if self._product_has_pending_mixing(
                    self.recipe_product_id, self.sale_order_id, exclude_id=self.id):
                blocked_name = self.recipe_product_id.display_name
                self.recipe_product_id = False
                self.bom_id = False
                self.mrp_production_id = False
                self.recipe_line_ids = [(5, 0, 0)]
                return {
                    'warning': {
                        'title': _('Mixing Not Completed'),
                        'message': _(
                            "A previous batch for %s on this Sale Order "
                            "hasn't had its Mixing marked as Done yet. "
                            "Complete mixing for that batch before "
                            "requisitioning a new one."
                        ) % blocked_name,
                    }
                }
        return res

    # def _assign_lots_to_picking(self, pick):
    #     """Fill lot-less move lines of tracked products from source-location
    #     quants, oldest lot first (FIFO)."""
    #     self.ensure_one()
    #     for move in pick.move_ids.filtered(
    #             lambda m: m.state not in ('done', 'cancel')
    #                       and m.product_id.tracking != 'none'
    #                       and not m.move_line_ids.filtered(lambda l: l.lot_id)):
    #         quants = self.env['stock.quant'].search([
    #             ('product_id', '=', move.product_id.id),
    #             ('location_id', '=', move.location_id.id),
    #             ('lot_id', '!=', False),
    #             ('quantity', '>', 0),
    #         ], order='in_date asc, id asc')
    #         remaining = move.product_uom_qty
    #         vals = []
    #         for q in quants:
    #             if remaining <= 0:
    #                 break
    #             take = min(remaining, q.quantity - q.reserved_quantity)
    #             if take <= 0:
    #                 continue
    #             vals.append((0, 0, {
    #                 'move_id': move.id,
    #                 'product_id': move.product_id.id,
    #                 'lot_id': q.lot_id.id,
    #                 'quantity': take,
    #                 'product_uom_id': move.product_uom.id,
    #                 'location_id': move.location_id.id,
    #                 'location_dest_id': move.location_dest_id.id,
    #             }))
    #             remaining -= take
    #         if vals:
    #             move.move_line_ids.filtered(lambda l: not l.lot_id).unlink()
    #             self.env['stock.move.line'].create(vals)

    # ---------- Hard safety net at confirm time ----------
    def action_confirm(self):
        # 1) Refresh stale draft lines automatically before confirmation.
        #    This preserves issued quantities and remarks by product.
        # for rec in self:
        #     if rec.bom_id and rec.recipe_line_ids:
        #         fresh = rec._requirements_dict(rec._exploded_requirements(rec.qty_producing or 0.0))
        #         current = rec._recipe_lines_dict()
        #
        #         if fresh != current:
        #             rec._populate_recipe_lines()
        #             fresh = rec._requirements_dict(rec._exploded_requirements(rec.qty_producing or 0.0))
        #             current = rec._recipe_lines_dict()
        #
        #             if fresh != current:
        #                 raise UserError(_(
        #                     "Recipe lines are OUT OF DATE: the BOM was changed after these "
        #                     "lines were generated. Set the MRS to Draft, nudge the Quantity "
        #                     "(or re-select the Recipe) to regenerate the lines, then confirm."))
        #
        # 2) Mixing gate: no new batch while a previous one is unmixed.
        for rec in self:
            if rec.sale_order_id and rec.recipe_product_id:
                if rec._product_has_pending_mixing(
                        rec.recipe_product_id, rec.sale_order_id, exclude_id=rec.id):
                    raise UserError(_(
                        "Cannot confirm: a previous batch for %s on %s still "
                        "has incomplete Mixing. Mark that batch's Mixing "
                        "Slip as Done first."
                    ) % (rec.recipe_product_id.display_name, rec.sale_order_id.name))

            # 3) Actual base confirmation: creates internal transfer and sets state.
            res = super().action_confirm()

            # 4) Reserve + FIFO lot-stamp the transfer so Validate never fails
            #    with "supply a Lot/Serial Number".
            # for rec in self:
            #     pick = rec.picking_id
            #     if pick and pick.state not in ('done', 'cancel'):
            #         pick.action_assign()
            #         rec._assign_lots_to_picking(pick)
            return res


    def action_open_mixing_slip(self):
        self.ensure_one()

        # 1. Check if a Mixing Slip already exists for this MRS
        existing_slip = self.env['mixing.slip'].search([('mrs_id', '=', self.id)], limit=1)

        # 2. If it does NOT exist, create it
        if not existing_slip:
            if not self.mrp_production_id:
                raise UserError(_("Cannot open/create a Mixing Slip because there is no MO linked to this MRS yet."))

            existing_slip = self.env['mixing.slip'].create({
                'sale_order_id': self.sale_order_id.id,
                'recipe_product_id': self.recipe_product_id.id,
                'mrs_id': self.id,
                'line_ids': [
                    (0, 0, {'move_id': move.id, 'sno': i})
                    for i, move in enumerate(self.mrp_production_id.move_raw_ids, start=1)
                ]
            })

        # 3. Open the Mixing Slip (either the existing one or the new one)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Mixing Slip'),
            'res_model': 'mixing.slip',
            'res_id': existing_slip.id,
            'view_mode': 'form',
            'target': 'current',
        }
    # ==========================================
    # MULTI-LEVEL BOM EXPLOSION (Kit dissolves into spices)
    # ==========================================
    def _get_demand_qty(self):
        self.ensure_one()
        qty = 0.0
        for fname in ('qty_producing', 'quantity', 'product_qty'):
            if fname in self._fields:
                qty = getattr(self, fname) or 0.0
                if qty:
                    break
        return qty or 1.0

    def _approved_kit_filter(self):
        """Extra domain so explosions only ever use APPROVED Kit versions
        (works with whatever rd_module names its approval field)."""
        for fname, fval in (('approval_status', 'approved'),
                            ('state', 'approved'),
                            ('is_approved', True)):
            if fname in self.env['mrp.bom']._fields:
                return [(fname, '=', fval)]
        return []

    def _find_kit_bom(self, product, parent_bom=None, preferred=None):
        self.ensure_one()
        if (preferred and preferred.active and preferred.type == 'phantom'
                and preferred.product_tmpl_id == product.product_tmpl_id):
            return preferred
        base = [('type', '=', 'phantom'), ('active', '=', True),
                ('company_id', 'in', [self.env.company.id, False])]
        base += self._approved_kit_filter()

    def _exploded_requirements(self, qty):
        """Ordered manual explosion for a given demand qty, mirroring Odoo's
        own mrp.bom.explode() algorithm bit-for-bit (same order of operations,
        same 'round UP only at the leaf' rule) so the MRS can never diverge
        from what the linked MO's 'To Consume' column shows for the same BOM.
        A blanket float_round(x, 2) at the end is NOT equivalent to this —
        Odoo never rounds at intermediate kit levels, and the final round is
        UP to the component's own UoM rounding, not nearest-2-decimal.

        Top-level BOM = finished/box packaging BOM.
        Any component with an approved Kit/phantom BOM (per SO line preference)
        is dissolved into raw materials, exactly like a phantom BOM would be
        by native explode() — just with our own BOM-version selection instead
        of Odoo's default _bom_find().
        """
        self.ensure_one()
        out = []

        if not self.bom_id:
            return out

        bom = self.bom_id
        preferred = self._preferred_kit_bom()

        # Same "factor" formula as mrp.production._get_moves_raw_values():
        #   factor = product_uom._compute_quantity(product_qty, bom.uom, round=False) / bom.product_qty
        if self.uom_id and bom.product_uom_id:
            qty_in_bom_uom = self.uom_id._compute_quantity(qty, bom.product_uom_id, round=False)
        else:
            qty_in_bom_uom = qty
        top_factor = (qty_in_bom_uom / bom.product_qty) if bom.product_qty else 0.0

        # Same worklist shape as native explode()'s `bom_lines` list.
        work = [(bom_line, top_factor) for bom_line in bom.bom_line_ids]

        while work:
            current_line, current_factor = work[0]
            work = work[1:]

            comp = current_line.product_id
            # Same multiply-first step as native explode():
            line_quantity = current_factor * current_line.product_qty

            kit = self._find_kit_bom(comp, preferred=preferred)

            if kit:
                # Same as native explode()'s `if bom:` branch: convert and
                # divide by the sub-BOM's own product_qty, WITHOUT rounding —
                # rounding only ever happens at the final leaf line.
                if current_line.product_uom_id and kit.product_uom_id:
                    converted_line_quantity = current_line.product_uom_id._compute_quantity(
                        line_quantity / kit.product_qty, kit.product_uom_id, round=False)
                else:
                    converted_line_quantity = (line_quantity / kit.product_qty) if kit.product_qty else 0.0
                work = [(kit_line, converted_line_quantity) for kit_line in kit.bom_line_ids] + work
            else:
                # Leaf / raw material: round UP to the component's OWN UoM
                # rounding precision — this, not a fixed 2-decimal round, is
                # what actually determines whether a value like "...444444"
                # lands on X.44 or X.45. Different components can have
                # different UoM rounding increments, which is the other
                # reason a single global rule can never match the MO for
                # every line.
                rounded_qty = current_line.product_uom_id.round(line_quantity, rounding_method='UP')
                out.append((comp, rounded_qty, current_line.product_uom_id))

        return out


    def _preferred_kit_bom(self):
        """The Kit version explicitly chosen on the SO line, if any."""
        self.ensure_one()
        if not (self.sale_order_id and self.recipe_product_id):
            return self.env['mrp.bom']
        so_line = self.sale_order_id.order_line.filtered(
            lambda l: l.product_id == self.recipe_product_id)[:1]
        return so_line.semi_bom_id if so_line else self.env['mrp.bom']

    def _requirements_dict(self, requirements):
        """Aggregate exploded requirements by product for safe comparison."""
        result = {}
        for product, qty, _uom in requirements:
            result[product.id] = round(result.get(product.id, 0.0) + (qty or 0.0), 2)
        return result

    def _recipe_lines_dict(self):
        """Aggregate current MRS recipe lines by product for safe comparison."""
        self.ensure_one()
        result = {}
        for line in self.recipe_line_ids.filtered(lambda l: l.product_id):
            result[line.product_id.id] = round(
                result.get(line.product_id.id, 0.0) + (line.quantity_required or 0.0),
                2
            )
        return result

    def _get_mo_reserved_qty(self, product):
        """Qty of `product` already reserved/consumed on the linked MO's raw
        moves (the 'Consumed' column in the MO's Components tab). Cancelled
        moves don't count — they were never actually reserved against."""
        self.ensure_one()
        if not self.mrp_production_id:
            return 0.0
        moves = self.mrp_production_id.move_raw_ids.filtered(
            lambda m: m.product_id == product and m.state != 'cancel')
        return sum(moves.mapped('quantity'))

    def _populate_recipe_lines(self):
        self.ensure_one()
        lines = [(5, 0, 0)]

        if not self.bom_id:
            self.recipe_line_ids = lines
            return

        # Preserve issued quantities and remarks.
        prev = {
            l.product_id.id: (l.quantity_issued, l.remarks)
            for l in self.recipe_line_ids
            if l.product_id
        }

        # Full-order requirement per component — this is what mo.move_raw_ids'
        # product_uom_qty (the "1,319.45" total) is, and it's already correct
        # since it matches explode()'s UP-rounding-at-leaf-only rule.
        total = self._exploded_requirements(self.kgs or 0.0)
        totals = {}
        uoms = {}
        for product, qty, uom in total:
            totals[product.id] = totals.get(product.id, 0.0) + (qty or 0.0)
            uoms[product.id] = uom

        mo = self.mrp_production_id
        order_qty = (mo.product_qty if mo else self.kgs) or 0.0
        already_produced = (mo.qty_produced if mo else 0.0) or 0.0
        denom = (order_qty - already_produced) or 1.0

        sno = 1
        for product_id, total_req in totals.items():
            if float_is_zero(total_req, precision_digits=2):
                continue

            # FIX: don't re-explode the BOM at the batch quantity. Match the
            # MO's own should_consume_qty formula exactly:
            #   unit_factor = product_uom_qty / (product_qty - qty_produced)
            #   should_consume_qty = uom.round(qty_producing * unit_factor, 'HALF-UP')
            # A fresh explosion at the batch qty is a structurally different
            # calculation from this linear scale-down and will land 0.01 off
            # from what the MO shows, even when the total matches perfectly.
            unit_factor = total_req / denom
            uom = uoms[product_id]
            batch_qty = uom.round(
                (self.qty_producing or 0.0) * unit_factor,
                rounding_method='HALF-UP'
            )

            if float_is_zero(batch_qty, precision_digits=2):
                continue

            comp = self.env['product.product'].browse(product_id)
            issued, remarks = prev.get(product_id, (0.0, ''))

            lines.append((0, 0, {
                'sno': sno,
                'product_id': comp.id,
                'item_code': comp.default_code or '',
                'item_description': comp.name or '',
                'uom_id': uom.id,
                'quantity_required': batch_qty,
                'quantity_required_total': total_req,
                'quantity_issued': issued,
                'remarks': remarks or '',
            }))
            sno += 1

        self.recipe_line_ids = lines