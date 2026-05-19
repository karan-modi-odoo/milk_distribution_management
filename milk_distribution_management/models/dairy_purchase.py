import logging
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class MilkDairyPurchase(models.Model):
    """
    Records the bill received from the dairy (e.g. Amulfed / Amul) each day.
    One record per dairy bill.  After confirmation the record is compared
    against the dispatch totals for the same date so the distributor can
    spot any discrepancy immediately.

    Stock integration (v19.0.8.0.0):
        On confirmation a stock.picking of type 'incoming' is created and
        immediately validated so that product quantities are available in
        the default stock location without any manual warehouse step.
        The picking is linked via picking_id for traceability.
    """
    _name = 'milk.dairy.purchase'
    _description = 'Dairy Purchase Bill'
    _order = 'date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(
        string='Bill Reference', default='New', readonly=True, copy=False,
        tracking=True,
    )
    date = fields.Date(
        string='Bill Date', required=True, default=fields.Date.today,
        tracking=True, index=True,
    )
    dairy_name = fields.Char(
        string='Dairy Name',
        default='Amulfed Dairy',
        tracking=True,
        help='Name of the dairy / supplier as printed on the bill.',
    )
    vehicle_no = fields.Char(string='Vehicle No.', tracking=True)
    driver_name = fields.Char(string='Driver Name', tracking=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed')],
        default='draft', string='Status', tracking=True,
    )

    line_ids = fields.One2many(
        'milk.dairy.purchase.line', 'purchase_id', string='Products',
    )

    # ── Stock receipt link ────────────────────────────────────────────────────
    picking_id = fields.Many2one(
        'stock.picking',
        string='Stock Receipt',
        readonly=True,
        copy=False,
        help='Stock receipt created automatically when this bill is confirmed.',
    )

    # ── Totals ────────────────────────────────────────────────────────────────

    total_qty = fields.Float(
        compute='_compute_totals', string='Total Qty / Crates',
        digits=(16, 2), store=True,
    )
    total_amount = fields.Float(
        compute='_compute_totals', string='Total Amount (Rs)',
        digits=(16, 2), store=True,
    )

    @api.depends('line_ids.qty', 'line_ids.amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped('qty'))
            rec.total_amount = sum(rec.line_ids.mapped('amount'))

    # ── Reconciliation against dispatch ───────────────────────────────────────

    dispatched_amount = fields.Float(
        compute='_compute_reconciliation',
        string='Dispatched Amount (Rs)',
        digits=(16, 2),
        help='Sum of all confirmed dispatch sheets for the same date.',
    )
    difference = fields.Float(
        compute='_compute_reconciliation',
        string='Difference (Rs)',
        digits=(16, 2),
        help='Dairy bill total minus dispatched total. Positive = over-billed by dairy.',
    )
    reconciled = fields.Boolean(
        compute='_compute_reconciliation',
        string='Reconciled',
        help='True when the difference is within Rs 1 (rounding tolerance).',
    )

    @api.depends('total_amount', 'date')
    def _compute_reconciliation(self):
        for rec in self:
            # Include both 'confirmed' and 'delivered' sheets so the reconciliation
            # figure matches the P&L's revenue source exactly.
            # Previously only 'confirmed' was included, which caused a false
            # discrepancy whenever sheets were marked 'delivered' on the same date.
            sheets = self.env['milk.dispatch.sheet'].search([
                ('date', '=', rec.date),
                ('state', 'in', ('confirmed', 'delivered')),
            ])
            dispatched = sum(sheets.mapped('total_amount'))
            rec.dispatched_amount = dispatched
            rec.difference = rec.total_amount - dispatched
            rec.reconciled = abs(rec.difference) <= 1.0

    # ── Sequence ─────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                        self.env['ir.sequence'].next_by_code('milk.dairy.purchase')
                        or 'New'
                )
        return super().create(vals_list)

    # ── Confirm ───────────────────────────────────────────────────────────────

    def action_confirm(self):
        for rec in self:
            if rec.state == 'confirmed':
                raise UserError(f"'{rec.name}' is already confirmed.")
            if not rec.line_ids:
                raise UserError("Add at least one product line before confirming.")

            # Create and immediately validate the stock receipt
            picking = rec._create_stock_receipt()
            rec.write({
                'state': 'confirmed',
                'picking_id': picking.id,
            })

            if not rec.reconciled:
                _logger.warning(
                    "Dairy purchase %s confirmed with reconciliation gap of Rs %.2f",
                    rec.name, rec.difference,
                )

            # Auto-alert: schedule a warning activity for the manager group
            # when the bill vs. dispatched difference exceeds Rs 1.0
            if abs(rec.difference) > 1.0:
                rec._create_reconciliation_alert()

    # ── Auto-alert: reconciliation warning activity ───────────────────────────

    def _create_reconciliation_alert(self):
        """
        Schedule a warning ``mail.activity`` on this purchase record when the
        absolute bill-vs-dispatched difference exceeds Rs 1.0.

        The activity is assigned to every user who belongs to the Milk ERP
        Manager group (``milk_distribution_management.group_milk_manager``).
        If no manager users are found, the activity falls back to the current
        user so the alert is never silently dropped.

        Called exclusively from :meth:`action_confirm`; must not alter any
        field, state, or workflow on the record.
        """
        self.ensure_one()

        # Resolve the built-in 'Warning' activity type (mail module ships it).
        # ``mail.mail_activity_data_warning`` is present in every standard Odoo
        # instance that has the mail module installed.
        activity_type = self.env.ref(
            'mail.mail_activity_data_warning', raise_if_not_found=False
        )
        if not activity_type:
            _logger.warning(
                "Dairy purchase %s: 'Warning' activity type not found — "
                "skipping reconciliation alert.",
                self.name,
            )
            return

        # Collect users in the Milk ERP Manager group.
        manager_group = self.env.ref(
            'milk_distribution_management.group_milk_manager',
            raise_if_not_found=False,
        )
        if manager_group:
            manager_users = manager_group.users
        else:
            manager_users = self.env['res.users']

        # Fallback: alert the current user if no managers are configured yet.
        if not manager_users:
            _logger.warning(
                "Dairy purchase %s: no users found in group_milk_manager — "
                "assigning reconciliation alert to current user (%s).",
                self.name, self.env.user.login,
            )
            manager_users = self.env.user

        note = (
            f"Bill vs. dispatched difference of Rs {abs(self.difference):.2f} "
            f"detected on dairy purchase <b>{self.name}</b> "
            f"(date: {self.date}). "
            f"Bill total: Rs {self.total_amount:.2f} | "
            f"Dispatched total: Rs {self.dispatched_amount:.2f}. "
            f"Please review and take corrective action."
        )

        for user in manager_users:
            self.activity_schedule(
                activity_type_id=activity_type.id,
                summary="Reconciliation Alert: Bill vs Dispatch Mismatch",
                note=note,
                user_id=user.id,
            )

        _logger.info(
            "Dairy purchase %s: reconciliation alert scheduled for %d manager(s) "
            "(difference=Rs %.2f).",
            self.name, len(manager_users), self.difference,
        )

    # ── Smart button ──────────────────────────────────────────────────────────

    def action_view_picking(self):
        """Open the linked stock receipt."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Stock Receipt',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.picking_id.id,
            'target': 'current',
        }

    # ── Internal: stock receipt creation ─────────────────────────────────────

    def _create_stock_receipt(self):
        """
        Create a stock.picking (type: incoming) for this dairy purchase bill
        and immediately validate it so stock is available right away.

        Source  : Virtual supplier location (stock.stock_location_suppliers)
        Destination: Default stock location of the first warehouse
                     (company.warehouse_ids[0].lot_stock_id)

        Each product line in the bill becomes one stock.move.
        Quantity unit is the product's UoM — the qty field on the dairy
        purchase line holds crate counts, so the product's UoM should be
        set to 'Crate' (or equivalent) for the stock figures to be correct.

        Returns the validated stock.picking record.
        """
        self.ensure_one()

        # ── Resolve locations ─────────────────────────────────────────────────
        # Supplier location: standard virtual location for incoming goods
        supplier_location = self.env.ref(
            'stock.stock_location_suppliers', raise_if_not_found=False
        )
        if not supplier_location:
            raise UserError(
                "Cannot find the default Supplier location (stock.stock_location_suppliers). "
                "Please ensure the 'stock' module is installed correctly."
            )

        # Destination: first warehouse's main stock location
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1
        )
        if not warehouse:
            raise UserError(
                "No warehouse is configured for this company. "
                "Please set up a warehouse under Inventory → Configuration → Warehouses."
            )
        dest_location = warehouse.lot_stock_id

        # ── Resolve the incoming picking type for this warehouse ──────────────
        picking_type = self.env['stock.picking.type'].search([
            ('warehouse_id', '=', warehouse.id),
            ('code', '=', 'incoming'),
        ], limit=1)
        if not picking_type:
            raise UserError(
                f"No 'Receipts' operation type found for warehouse "
                f"'{warehouse.name}'. "
                "Check Inventory → Configuration → Operations Types."
            )

        # ── Build stock move lines ────────────────────────────────────────────
        move_vals = []
        for line in self.line_ids:
            if line.qty <= 0:
                continue
            move_vals.append((0, 0, {
                'product_id': line.product_id.id,
                'product_uom': line.product_id.uom_id.id,
                'product_uom_qty': line.qty,
                'location_id': supplier_location.id,
                'location_dest_id': dest_location.id,
            }))

        if not move_vals:
            raise UserError(
                "No valid product lines with quantity > 0 found. "
                "Cannot create a stock receipt."
            )

        # ── Create the picking ────────────────────────────────────────────────
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': picking_type.id,
            'partner_id': False,  # dairy is a Char, not a partner yet
            'origin': self.name,
            'scheduled_date': self.date,
            'location_id': supplier_location.id,
            'location_dest_id': dest_location.id,
            'move_ids': move_vals,
        })

        # ── Set done quantities and validate immediately ───────────────────────
        # immediate_transfer=True skips the "set quantities" wizard so the
        # receipt is validated in one step without requiring user interaction.
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty

        picking.sudo().button_validate()

        _logger.info(
            "Dairy purchase %s: stock receipt %s validated — %d product(s) → %s",
            self.name, picking.name, len(picking.move_ids), dest_location.complete_name,
        )
        return picking

    # ── Write guard ───────────────────────────────────────────────────────────

    _CONFIRMED_BLOCKED_FIELDS = frozenset({
        'date', 'dairy_name', 'vehicle_no', 'driver_name', 'line_ids',
    })

    def write(self, vals):
        blocked = self._CONFIRMED_BLOCKED_FIELDS.intersection(vals.keys())
        if blocked:
            for rec in self:
                if rec.state == 'confirmed':
                    raise UserError(
                        f"'{rec.name}' is confirmed and cannot be edited.\n\n"
                        f"Locked fields: {', '.join(sorted(blocked))}"
                    )
        return super().write(vals)


class MilkDairyPurchaseLine(models.Model):
    """One product row on a dairy purchase bill."""
    _name = 'milk.dairy.purchase.line'
    _description = 'Dairy Purchase Line'

    purchase_id = fields.Many2one(
        'milk.dairy.purchase', string='Purchase Bill', ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
    )
    qty = fields.Float(
        string='Qty / Crates', digits=(16, 2), required=True,
    )
    rate = fields.Float(
        string='Rate (Rs)', digits=(16, 2), required=True,
    )
    amount = fields.Float(
        string='Amount (Rs)', compute='_compute_amount',
        digits=(16, 2), store=True,
    )

    @api.onchange('product_id')
    def _onchange_product(self):
        if self.product_id:
            self.rate = self.product_id.standard_price  # cost price from product

    @api.depends('qty', 'rate')
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.qty * rec.rate

    @api.constrains('qty', 'rate')
    def _check_values(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError("Quantity must be greater than zero.")
            if rec.rate <= 0:
                raise ValidationError("Rate must be greater than zero.")

    @api.constrains('purchase_id', 'product_id')
    def _check_unique_product_per_bill(self):
        for rec in self:
            if self.search_count([
                ('purchase_id', '=', rec.purchase_id.id),
                ('product_id', '=', rec.product_id.id),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("The same product cannot appear twice on one dairy bill.")
