import logging
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

CRATE_MOVE_TYPE = [
    ('issue', 'Issue to Dealer'),
    ('return', 'Return from Dealer'),
]


class MilkCrateTransaction(models.Model):
    """
    Records every crate movement (issue / return) for a dealer.
    Each row carries the running balance after the transaction so the
    ledger can be reconstructed without cursor-style iteration.
    """
    _name = 'milk.crate.transaction'
    _description = 'Milk Crate Transaction'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', default='New', readonly=True, copy=False,
    )
    date = fields.Date(
        string='Date', required=True, default=fields.Date.today,
        tracking=True, index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Dealer', required=True,
        tracking=True, index=True,
    )
    move_type = fields.Selection(
        CRATE_MOVE_TYPE, string='Type', required=True, tracking=True,
    )
    qty = fields.Integer(
        string='Crates', required=True,
        help='Number of crates issued or returned.',
    )
    dispatch_sheet_id = fields.Many2one(
        'milk.dispatch.sheet', string='Dispatch Sheet',
        readonly=True, copy=False,
        help='Auto-populated when created from a dispatch sheet.',
    )
    notes = fields.Char(string='Notes')
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed')],
        default='draft', string='Status', tracking=True,
    )
    balance_after = fields.Integer(
        string='Balance After (Crates)', readonly=True,
        help='Running crate balance with the dealer after this transaction.',
    )

    # ── Constraints ──────────────────────────────────────────────────────────

    @api.constrains('qty')
    def _check_qty(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError("Crate quantity must be greater than zero.")

    @api.constrains('name')
    def _check_unique_name(self):
        for rec in self:
            if self.search_count([
                ('name', '=', rec.name),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("Crate transaction reference must be unique.")

    # ── Sequence ─────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                        self.env['ir.sequence'].next_by_code('milk.crate.transaction')
                        or 'New'
                )
        return super().create(vals_list)

    # ── Confirm ───────────────────────────────────────────────────────────────

    def action_confirm(self):
        for rec in self:
            if rec.state == 'confirmed':
                raise UserError(f"'{rec.name}' is already confirmed.")
            balance = rec._get_balance_before()
            if rec.move_type == 'return' and rec.qty > balance:
                raise UserError(
                    f"Cannot return {rec.qty} crate(s) for '{rec.partner_id.name}'.\n"
                    f"Current balance with dealer: {balance} crate(s)."
                )
            new_balance = (
                balance + rec.qty if rec.move_type == 'issue'
                else balance - rec.qty
            )
            rec.write({'state': 'confirmed', 'balance_after': new_balance})
            _logger.info(
                "Crate %s confirmed — dealer: %s  before: %d  after: %d",
                rec.name, rec.partner_id.name, balance, new_balance,
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_balance_before(self):
        """Latest confirmed balance for this dealer before this record.

        The domain is bounded by ``('date', '<=', self.date)`` so that any
        future-dated confirmed transaction cannot contaminate the running
        balance calculation for an earlier date.  Within the same date,
        ``id desc`` ordering ensures the most recently created confirmed
        transaction (excluding self) is used as the prior balance.
        """
        self.ensure_one()
        last = self.search([
            ('partner_id', '=', self.partner_id.id),
            ('state', '=', 'confirmed'),
            ('date', '<=', self.date),
            ('id', '!=', self.id),
        ], order='date desc, id desc', limit=1)
        return last.balance_after if last else 0


class MilkCrateLedger(models.Model):
    """
    Daily crate summary per dealer: opening, issued, returned, closing.
    Populated by the 'Generate' wizard (milk.crate.ledger.wizard).
    Stored as regular records so the accountant can review history.
    """
    _name = 'milk.crate.ledger'
    _description = 'Milk Crate Ledger'
    _order = 'date desc, partner_id'
    _rec_name = 'partner_id'

    partner_id = fields.Many2one(
        'res.partner', string='Dealer', required=True, index=True,
    )
    date = fields.Date(string='Date', required=True, index=True)
    opening = fields.Integer(string='Opening Crates')
    issued = fields.Integer(string='Issued')
    returned = fields.Integer(string='Returned')
    closing = fields.Integer(
        string='Closing Crates',
        compute='_compute_closing', store=True,
    )

    @api.depends('opening', 'issued', 'returned')
    def _compute_closing(self):
        for rec in self:
            rec.closing = rec.opening + rec.issued - rec.returned

    @api.constrains('partner_id', 'date')
    def _check_unique_dealer_date(self):
        for rec in self:
            if self.search_count([
                ('partner_id', '=', rec.partner_id.id),
                ('date', '=', rec.date),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("A crate ledger entry for this dealer and date already exists.")


class MilkCrateLedgerWizard(models.TransientModel):
    """
    Wizard to generate / refresh the daily crate ledger for a date range.
    Existing records in the range are deleted and rebuilt from transactions.
    """
    _name = 'milk.crate.ledger.wizard'
    _description = 'Generate Crate Ledger'

    date_from = fields.Date(
        string='From Date', required=True, default=fields.Date.today,
    )
    date_to = fields.Date(
        string='To Date', required=True, default=fields.Date.today,
    )

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from > rec.date_to:
                raise ValidationError("'From Date' must be on or before 'To Date'.")

    def action_generate(self):
        self.ensure_one()
        Ledger = self.env['milk.crate.ledger']
        Txn = self.env['milk.crate.transaction']

        # Delete existing ledger rows in the range so we can rebuild cleanly
        Ledger.search([
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]).unlink()

        # Fetch all confirmed transactions in range grouped by partner + date
        txns = Txn.search([
            ('state', '=', 'confirmed'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ], order='date asc, id asc')

        # Aggregate per (partner_id, date)
        summary = {}  # {(partner_id, date): {'issued': 0, 'returned': 0}}
        for t in txns:
            key = (t.partner_id.id, t.date)
            if key not in summary:
                summary[key] = {'issued': 0, 'returned': 0}
            if t.move_type == 'issue':
                summary[key]['issued'] += t.qty
            else:
                summary[key]['returned'] += t.qty

        # Build ledger lines — opening = balance_after of last txn before date
        lines = []
        for (partner_id, date), data in sorted(summary.items(), key=lambda x: x[0]):
            last_before = Txn.search([
                ('partner_id', '=', partner_id),
                ('state', '=', 'confirmed'),
                ('date', '<', date),
            ], order='date desc, id desc', limit=1)
            opening = last_before.balance_after if last_before else 0
            lines.append({
                'partner_id': partner_id,
                'date': date,
                'opening': opening,
                'issued': data['issued'],
                'returned': data['returned'],
            })

        if lines:
            Ledger.create(lines)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Crate Ledger',
            'res_model': 'milk.crate.ledger',
            'view_mode': 'list,form',
            'domain': [
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
            ],
            'target': 'current',
        }
