import logging
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class MilkDairyOrder(models.Model):
    """
    Gap #1 — Dairy Order

    Records the order the distributor places with the dairy AFTER collecting
    dealer orders via Dispatch Sheets.

    Workflow:
        Draft  → (Generate from Dispatch Sheets, edit if needed)
               → Mark as Sent  (order placed with dairy)
               → Mark as Fulfilled  (Dairy Purchase Bill received and linked)

    Comparison:
        Each line shows Ordered Qty vs Dispatched Qty vs Billed Qty so the
        distributor can immediately spot any discrepancy.
    """
    _name = 'milk.dairy.order'
    _description = 'Dairy Order'
    _order = 'date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(
        string='Order Reference',
        default='New',
        readonly=True,
        copy=False,
        tracking=True,
    )
    date = fields.Date(
        string='Order Date',
        required=True,
        default=fields.Date.today,
        tracking=True,
        index=True,
    )
    dairy_name = fields.Char(
        string='Dairy Name',
        default='Amulfed Dairy',
        tracking=True,
        help='Name of the dairy this order is being placed with.',
    )
    notes = fields.Text(
        string='Notes / Instructions',
        help='Vehicle details, special requests, or any instructions for the dairy.',
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('fulfilled', 'Fulfilled'),
        ],
        default='draft',
        string='Status',
        tracking=True,
    )

    line_ids = fields.One2many(
        'milk.dairy.order.line',
        'order_id',
        string='Products',
    )

    # ── Link to Dairy Purchase Bill ───────────────────────────────────────────
    purchase_id = fields.Many2one(
        'milk.dairy.purchase',
        string='Dairy Purchase Bill',
        copy=False,
        tracking=True,
        readonly=False,
        help='Link the Dairy Purchase Bill here once it arrives to compare '
             'ordered vs billed quantities.',
    )

    # ── Totals ────────────────────────────────────────────────────────────────
    total_ordered_qty = fields.Float(
        compute='_compute_totals',
        string='Total Ordered (Crates)',
        digits=(16, 2),
        store=True,
    )
    total_ordered_amount = fields.Float(
        compute='_compute_totals',
        string='Total Amount (Rs)',
        digits=(16, 2),
        store=True,
    )

    @api.depends('line_ids.ordered_qty', 'line_ids.ordered_amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_ordered_qty = sum(rec.line_ids.mapped('ordered_qty'))
            rec.total_ordered_amount = sum(rec.line_ids.mapped('ordered_amount'))

    # ── Sequence ─────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                        self.env['ir.sequence'].next_by_code('milk.dairy.order')
                        or 'New'
                )
        return super().create(vals_list)

    # ── Write guard ───────────────────────────────────────────────────────────
    _FULFILLED_BLOCKED_FIELDS = frozenset({'date', 'dairy_name', 'line_ids'})

    def write(self, vals):
        blocked = self._FULFILLED_BLOCKED_FIELDS.intersection(vals.keys())
        if blocked:
            for rec in self:
                if rec.state == 'fulfilled':
                    raise UserError(
                        f"'{rec.name}' is fulfilled and cannot be edited.\n\n"
                        f"Locked fields: {', '.join(sorted(blocked))}"
                    )
        return super().write(vals)

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_generate_from_sheets(self):
        """
        Auto-aggregate product quantities from all confirmed Dispatch Sheets
        for the same date, grouped by product.

        Existing lines are replaced. User may edit quantities before sending.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise UserError("Only draft orders can be regenerated.")

        sheets = self.env['milk.dispatch.sheet'].search([
            ('date', '=', self.date),
            ('state', 'in', ('confirmed', 'delivered')),
        ])

        if not sheets:
            raise UserError(
                f"No confirmed Dispatch Sheets found for {self.date}.\n\n"
                "Confirm at least one Dispatch Sheet for this date first."
            )

        # Aggregate qty per product across all sheets → all dealer lines → all product lines
        product_totals = {}  # {product_id: {'qty': float, 'rate': float}}
        for sheet in sheets:
            for dealer_line in sheet.line_ids:
                for pl in dealer_line.product_line_ids:
                    if pl.qty <= 0:
                        continue
                    pid = pl.product_id.id
                    if pid not in product_totals:
                        # Derive rate: prefer cost price from product, fallback to line rate
                        product_totals[pid] = {
                            'product_id': pid,
                            'qty': 0.0,
                            'rate': pl.product_id.standard_price or pl.rate,
                        }
                    product_totals[pid]['qty'] += pl.qty

        if not product_totals:
            raise UserError(
                "Confirmed Dispatch Sheets for this date have no product lines with qty > 0."
            )

        new_lines = [
            (0, 0, {
                'product_id': data['product_id'],
                'ordered_qty': data['qty'],
                'rate': data['rate'],
            })
            for data in product_totals.values()
        ]

        # (5,0,0) clears existing lines before adding the new ones
        self.write({'line_ids': [(5, 0, 0)] + new_lines})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': (
                    f"Generated {len(new_lines)} product line(s) from "
                    f"{len(sheets)} confirmed Dispatch Sheet(s) for {self.date}."
                ),
                'sticky': False,
            },
        }

    def action_mark_sent(self):
        """Mark the order as sent to the dairy."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(
                    f"'{rec.name}' is already '{rec.state}'. Only draft orders can be sent."
                )
            if not rec.line_ids:
                raise UserError("Add at least one product line before marking as sent.")
            rec.state = 'sent'

    def action_mark_fulfilled(self):
        """Mark the order as fulfilled once the Dairy Purchase Bill has arrived."""
        for rec in self:
            if rec.state != 'sent':
                raise UserError(
                    f"Only sent orders can be marked as fulfilled. "
                    f"'{rec.name}' is currently '{rec.state}'."
                )
            rec.state = 'fulfilled'

    def action_reset_draft(self):
        """Reset a sent order back to draft for correction."""
        for rec in self:
            if rec.state != 'sent':
                raise UserError(
                    f"Only sent orders can be reset to draft. "
                    f"'{rec.name}' is currently '{rec.state}'."
                )
            rec.state = 'draft'

    def action_view_purchase_bill(self):
        """Open the linked Dairy Purchase Bill."""
        self.ensure_one()
        if not self.purchase_id:
            return
        return {
            'type': 'ir.actions.act_window',
            'name': 'Dairy Purchase Bill',
            'res_model': 'milk.dairy.purchase',
            'view_mode': 'form',
            'res_id': self.purchase_id.id,
            'target': 'current',
        }

    # ── Constraints ───────────────────────────────────────────────────────────
    @api.constrains('date', 'dairy_name')
    def _check_unique_date_dairy(self):
        for rec in self:
            if self.search_count([
                ('date', '=', rec.date),
                ('dairy_name', '=', rec.dairy_name),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("A Dairy Order for this date and dairy already exists.")


class MilkDairyOrderLine(models.Model):
    """One product row on a Dairy Order."""
    _name = 'milk.dairy.order.line'
    _description = 'Dairy Order Line'
    _order = 'product_id'

    order_id = fields.Many2one(
        'milk.dairy.order',
        string='Dairy Order',
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
    )
    ordered_qty = fields.Float(
        string='Ordered (Crates)',
        digits=(16, 2),
        required=True,
        default=0.0,
    )
    rate = fields.Float(
        string='Rate (Rs)',
        digits=(16, 2),
    )
    ordered_amount = fields.Float(
        string='Amount (Rs)',
        compute='_compute_ordered_amount',
        digits=(16, 2),
        store=True,
    )

    # ── Comparison columns (computed, read-only) ──────────────────────────────
    dispatched_qty = fields.Float(
        string='Dispatched (Crates)',
        compute='_compute_comparison',
        digits=(16, 2),
        help='Total qty dispatched for this product on the order date '
             '(from confirmed/delivered Dispatch Sheets).',
    )
    billed_qty = fields.Float(
        string='Billed (Crates)',
        compute='_compute_comparison',
        digits=(16, 2),
        help='Qty on the linked Dairy Purchase Bill for this product.',
    )
    qty_difference = fields.Float(
        string='Diff (Ordered − Billed)',
        compute='_compute_comparison',
        digits=(16, 2),
        help='Positive = ordered more than dairy billed. '
             'Negative = dairy billed more than ordered.',
    )

    @api.depends('ordered_qty', 'rate')
    def _compute_ordered_amount(self):
        for rec in self:
            rec.ordered_amount = rec.ordered_qty * rec.rate

    @api.depends('order_id.date', 'order_id.purchase_id', 'product_id')
    def _compute_comparison(self):
        for rec in self:
            if not rec.product_id or not rec.order_id:
                rec.dispatched_qty = 0.0
                rec.billed_qty = 0.0
                rec.qty_difference = 0.0
                continue

            # ── Dispatched qty: sum from confirmed/delivered sheets ────────────
            sheets = self.env['milk.dispatch.sheet'].search([
                ('date', '=', rec.order_id.date),
                ('state', 'in', ('confirmed', 'delivered')),
            ])
            dispatched = 0.0
            for sheet in sheets:
                for dl in sheet.line_ids:
                    for pl in dl.product_line_ids:
                        if pl.product_id.id == rec.product_id.id:
                            dispatched += pl.qty
            rec.dispatched_qty = dispatched

            # ── Billed qty: from linked Dairy Purchase Bill ───────────────────
            billed = 0.0
            if rec.order_id.purchase_id:
                for bill_line in rec.order_id.purchase_id.line_ids:
                    if bill_line.product_id.id == rec.product_id.id:
                        billed += bill_line.qty
            rec.billed_qty = billed

            rec.qty_difference = rec.ordered_qty - billed

    @api.onchange('product_id')
    def _onchange_product(self):
        if self.product_id:
            self.rate = self.product_id.standard_price

    @api.constrains('ordered_qty')
    def _check_ordered_qty(self):
        for rec in self:
            if rec.ordered_qty <= 0:
                raise ValidationError(
                    f"Ordered quantity must be greater than zero "
                    f"(product: {rec.product_id.display_name})."
                )

    @api.constrains('order_id', 'product_id')
    def _check_unique_product_per_order(self):
        for rec in self:
            if self.search_count([
                ('order_id', '=', rec.order_id.id),
                ('product_id', '=', rec.product_id.id),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("The same product cannot appear twice on one Dairy Order.")
