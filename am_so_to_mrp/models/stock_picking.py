from odoo import models, fields, api
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        for picking in self:
            if picking.state == "inspection_failed":
                raise UserError("Vehicle Inspection Failed. Contact the Vendor for a Fresh Delivery.")

            if picking.vehicle_inspection_id and picking.vehicle_inspection_id.state == 'draft':
                raise UserError(
                    'Record is on hold while Received Vehicle is being Inspected, Kindly complete the inspection and try again.')

            if picking.picking_type_id.code == "incoming":
                for move_line in picking.move_line_ids:
                    product = move_line.product_id

                    if product.tracking != "lot":
                        continue
                    if move_line.lot_id or move_line.lot_name:
                        continue

                    prefix = product.lot_prefix or "LOT"
                    today = fields.Date.context_today(self).strftime("%Y%m%d")
                    code = product.default_code or product.id

                    # --- CHANGED: DYNAMIC DAILY COUNTER RESET ---
                    # Instead of a database sequence, find the last sequence used TODAY for this product
                    search_pattern = f"{prefix}/{code}-{today}-%"
                    last_lot = self.env["stock.lot"].search([
                        ("name", "like", search_pattern),
                        ("product_id", "=", product.id),
                        ("company_id", "in", [picking.company_id.id, False])
                    ], order="name desc", limit=1)

                    if last_lot:
                        # Extract the last 5 digits from the name string, convert to integer, and add 1
                        try:
                            last_seq_str = last_lot.name.split("-")[-1]
                            next_seq_num = int(last_seq_str) + 1
                        except (ValueError, IndexError):
                            next_seq_num = 1
                    else:
                        # If no lot exists yet for today, start completely fresh at 1
                        next_seq_num = 1
                    # ---------------------------------------------

                    # Collision safety fallback tracker
                    lot_name = False
                    counter = 0
                    while True:
                        # Format number to be exactly 5 digits long (e.g., 00001)
                        current_seq = str(next_seq_num + counter).zfill(5)
                        temp_name = f"{prefix}/{code}-{today}-{current_seq}"

                        existing_lot = self.env["stock.lot"].search([
                            ("name", "=", temp_name),
                            ("product_id", "=", product.id),
                            ("company_id", "in", [picking.company_id.id, False])
                        ], limit=1)

                        if not existing_lot:
                            lot_name = temp_name
                            break

                        counter += 1

                    # 1. Explicitly create the lot record now
                    new_lot = self.env["stock.lot"].create({
                        'name': lot_name,
                        'product_id': product.id,
                        'company_id': picking.company_id.id,
                    })

                    # 2. Assign the actual lot record to the move line
                    move_line.lot_id = new_lot.id

                    # 3. Push this lot to any existing QC records for this product
                    qc_records = self.env['kalsal.quality.check'].search([
                        ('picking_ids', 'in', picking.id),
                        ('product_id', '=', product.id)
                    ])
                    if qc_records:
                        qc_records.write({'lot_id': [(4, new_lot.id)]})

        return super().button_validate()
