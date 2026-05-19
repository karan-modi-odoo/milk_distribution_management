import logging
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class MilkDairyLedger(models.Model):
    """
    Dairy Supplier Payable Ledger (milk.dairy.ledger)
    ==================================================
    Tracks the running payable balance owed by the distributor to the dairy.

    One ledger entry per dairy per date.  Entries are keyed on ``dairy_name``
    (a Char field, consistent with how milk.dairy.purchase and
    milk.dairy.transfer already identify the dairy) so no partner migration
    is required.

    Entry lifecycle
    ---------------
    * **Dairy Purchase Bill confirmed** → ``bill_amount`` added to the entry
      for that dairy + date.  This increases the outstanding payable.
    * **Dairy Transfer confirmed** → ``paid_amount`` added to the entry for
      that dairy + date.  This reduces the outstanding payable.

    Running balance
    ---------------
    ``opening_balance``  — closing balance of the most recent prior entry for
                          the same dairy (0.0 if first ever entry).
    ``bill_amount``      — total bills confirmed for this dairy on this date.
    ``paid_amount``      — total transfers confirmed for this dairy on this date.
    ``closing_balance``  — opening + bill_amount − paid_amount  (computed, stored).

    Design constraints
    ------------------
    * Does NOT modify dairy_purchase.action_confirm() or
      bank_settlement.MilkDairyTransfer.action_confirm() directly.
      Instead, it overrides those methods via inheritance (_inherit) hooks
      placed in THIS file so all existing logic stays 100 % untouched.
    * Idempotency: a single entry per dairy+date is always upserted, never
      duplicated.
    * Thread-safe: writes go through sudo() to avoid ACL issues during
      automated trigger calls; the originating user's action is still the one
      that triggers the write.
    """

    _name = 'milk.dairy.ledger'
    _description = 'Dairy Supplier Ledger'
    _order = 'date desc, dairy_name'
    _rec_name = 'dairy_name'
    _inherit = ['mail.thread']

    # ── Identity ──────────────────────────────────────────────────────────────

    dairy_name = fields.Char(
        string='Dairy Name',
        required=True,
        index=True,
        tracking=True,
    )
    date = fields.Date(
        string='Date',
        required=True,
        index=True,
        tracking=True,
    )

    # ── Source links (optional, for drill-down traceability) ──────────────────

    purchase_ids = fields.Many2many(
        'milk.dairy.purchase',
        'milk_dairy_ledger_purchase_rel',
        'ledger_id', 'purchase_id',
        string='Purchase Bills',
        readonly=True,
        copy=False,
        help='Confirmed dairy purchase bills that contributed to this entry.',
    )
    transfer_ids = fields.Many2many(
        'milk.dairy.transfer',
        'milk_dairy_ledger_transfer_rel',
        'ledger_id', 'transfer_id',
        string='Transfers / Payments',
        readonly=True,
        copy=False,
        help='Confirmed dairy transfers that reduced the payable on this entry.',
    )

    # ── Amounts ───────────────────────────────────────────────────────────────

    opening_balance = fields.Float(
        string='Opening Balance (Rs)',
        digits=(16, 2),
        tracking=True,
        help='Closing balance carried forward from the previous entry for this dairy.',
    )
    bill_amount = fields.Float(
        string='Bills (Rs)',
        digits=(16, 2),
        tracking=True,
        help='Total amount of confirmed dairy purchase bills on this date.',
    )
    paid_amount = fields.Float(
        string='Paid (Rs)',
        digits=(16, 2),
        tracking=True,
        help='Total amount transferred / paid to the dairy on this date.',
    )
    closing_balance = fields.Float(
        string='Closing Balance (Rs)',
        compute='_compute_closing',
        digits=(16, 2),
        store=True,
        help='Opening Balance + Bills − Paid.  This is the live outstanding payable.',
    )

    @api.depends('opening_balance', 'bill_amount', 'paid_amount')
    def _compute_closing(self):
        for rec in self:
            rec.closing_balance = (
                    rec.opening_balance + rec.bill_amount - rec.paid_amount
            )

    # ── SQL constraint ────────────────────────────────────────────────────────

    @api.constrains('dairy_name', 'date')
    def _check_unique_dairy_date(self):
        for rec in self:
            if self.search_count([
                ('dairy_name', '=', rec.dairy_name),
                ('date', '=', rec.date),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("A dairy ledger entry for this dairy and date already exists.")

    # ── Public API called from model hooks below ──────────────────────────────

    @api.model
    def _post_purchase(self, purchase):
        """
        Create or update the dairy ledger entry when a dairy purchase bill is
        confirmed.

        Args:
            purchase: a single milk.dairy.purchase record (already confirmed).
        """
        self._upsert_entry(
            dairy_name=purchase.dairy_name,
            date=purchase.date,
            bill_delta=purchase.total_amount,
            paid_delta=0.0,
            purchase=purchase,
            transfer=None,
        )

    @api.model
    def _post_transfer(self, transfer):
        """
        Create or update the dairy ledger entry when a dairy transfer is
        confirmed.

        Args:
            transfer: a single milk.dairy.transfer record (already confirmed).
        """
        self._upsert_entry(
            dairy_name=transfer.dairy_name,
            date=transfer.date,
            bill_delta=0.0,
            paid_delta=transfer.amount,
            purchase=None,
            transfer=transfer,
        )

    # ── Internal upsert ───────────────────────────────────────────────────────

    @api.model
    def _upsert_entry(
            self, dairy_name, date, bill_delta, paid_delta, purchase, transfer
    ):
        """
        Upsert (create or update) the ledger entry for ``dairy_name`` on
        ``date``, adding ``bill_delta`` to ``bill_amount`` and ``paid_delta``
        to ``paid_amount``.

        If no entry exists yet, the opening balance is derived from the most
        recent prior entry for the same dairy (0.0 if none exists).
        """
        Ledger = self.sudo()

        existing = Ledger.search([
            ('dairy_name', '=', dairy_name),
            ('date', '=', date),
        ], limit=1)

        # Prepare Many2many link commands
        purchase_cmd = [(4, purchase.id)] if purchase else []
        transfer_cmd = [(4, transfer.id)] if transfer else []

        if existing:
            write_vals = {}
            if bill_delta:
                write_vals['bill_amount'] = existing.bill_amount + bill_delta
            if paid_delta:
                write_vals['paid_amount'] = existing.paid_amount + paid_delta
            if purchase_cmd:
                write_vals['purchase_ids'] = purchase_cmd
            if transfer_cmd:
                write_vals['transfer_ids'] = transfer_cmd
            if write_vals:
                existing.write(write_vals)
            _logger.info(
                'Dairy ledger updated: dairy=%s date=%s Δbill=%.2f Δpaid=%.2f',
                dairy_name, date, bill_delta, paid_delta,
            )
        else:
            # Derive opening balance from last prior entry for this dairy
            last = Ledger.search([
                ('dairy_name', '=', dairy_name),
                ('date', '<', date),
            ], order='date desc', limit=1)
            opening = last.closing_balance if last else 0.0

            create_vals = {
                'dairy_name': dairy_name,
                'date': date,
                'opening_balance': opening,
                'bill_amount': bill_delta,
                'paid_amount': paid_delta,
            }
            if purchase_cmd:
                create_vals['purchase_ids'] = purchase_cmd
            if transfer_cmd:
                create_vals['transfer_ids'] = transfer_cmd

            Ledger.create(create_vals)
            _logger.info(
                'Dairy ledger created: dairy=%s date=%s opening=%.2f '
                'bill=%.2f paid=%.2f',
                dairy_name, date, opening, bill_delta, paid_delta,
            )


# ── Hook: milk.dairy.purchase — trigger on confirmation ───────────────────────

class MilkDairyPurchaseLedgerHook(models.Model):
    """
    Extends milk.dairy.purchase to trigger a dairy ledger entry on
    confirmation.  No existing field, method, or workflow is modified.
    Only action_confirm() gains a non-intrusive post-confirmation call.
    """

    _inherit = 'milk.dairy.purchase'

    # Smart-button computed field — shows ledger entry count for this bill
    dairy_ledger_count = fields.Integer(
        compute='_compute_dairy_ledger_count',
        string='Ledger Entries',
    )

    def _compute_dairy_ledger_count(self):
        for rec in self:
            # rec.id is a NewId on unsaved records — psycopg2 cannot adapt it.
            # Only query the DB when the record is already persisted.
            if not isinstance(rec.id, int):
                rec.dairy_ledger_count = 0
                continue
            rec.dairy_ledger_count = self.env['milk.dairy.ledger'].search_count([
                ('purchase_ids', 'in', [rec.id]),
            ])

    def action_confirm(self):
        """
        Calls super() first (all existing logic runs unchanged), then
        creates/updates the dairy ledger entry for each confirmed bill.
        """
        super().action_confirm()
        for rec in self:
            # Only post for records that are now confirmed (super() may skip
            # some records — e.g. already-confirmed ones raise UserError before
            # reaching this point, so this guard is a safety net only).
            if rec.state == 'confirmed':
                self.env['milk.dairy.ledger']._post_purchase(rec)

    def action_view_dairy_ledger(self):
        """Open dairy ledger entries linked to this purchase bill."""
        self.ensure_one()
        ledger_ids = self.env['milk.dairy.ledger'].search([
            ('purchase_ids', 'in', [self.id]),
        ]).ids
        return {
            'type': 'ir.actions.act_window',
            'name': 'Dairy Ledger',
            'res_model': 'milk.dairy.ledger',
            'view_mode': 'list,form',
            'domain': [('id', 'in', ledger_ids)],
            'target': 'current',
        }


# ── Hook: milk.dairy.transfer — trigger on confirmation ───────────────────────

class MilkDairyTransferLedgerHook(models.Model):
    """
    Extends milk.dairy.transfer to trigger a dairy ledger entry on
    confirmation.  No existing field, method, or workflow is modified.
    Only action_confirm() gains a non-intrusive post-confirmation call.
    """

    _inherit = 'milk.dairy.transfer'

    # Smart-button computed field — shows ledger entry count for this transfer
    dairy_ledger_count = fields.Integer(
        compute='_compute_dairy_ledger_count',
        string='Ledger Entries',
    )

    def _compute_dairy_ledger_count(self):
        for rec in self:
            # rec.id is a NewId on unsaved records — psycopg2 cannot adapt it.
            # Only query the DB when the record is already persisted.
            if not isinstance(rec.id, int):
                rec.dairy_ledger_count = 0
                continue
            rec.dairy_ledger_count = self.env['milk.dairy.ledger'].search_count([
                ('transfer_ids', 'in', [rec.id]),
            ])

    def action_confirm(self):
        """
        Calls super() first (all existing logic runs unchanged), then
        creates/updates the dairy ledger entry for each confirmed transfer.
        """
        super().action_confirm()
        for rec in self:
            if rec.state == 'confirmed':
                self.env['milk.dairy.ledger']._post_transfer(rec)

    def action_view_dairy_ledger(self):
        """Open dairy ledger entries linked to this transfer."""
        self.ensure_one()
        ledger_ids = self.env['milk.dairy.ledger'].search([
            ('transfer_ids', 'in', [self.id]),
        ]).ids
        return {
            'type': 'ir.actions.act_window',
            'name': 'Dairy Ledger',
            'res_model': 'milk.dairy.ledger',
            'view_mode': 'list,form',
            'domain': [('id', 'in', ledger_ids)],
            'target': 'current',
        }
