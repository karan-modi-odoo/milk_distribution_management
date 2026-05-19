"""
milk_distribution_management — Periodic Reports
================================================
Three accurate reports covering Monthly / Quarterly / Yearly periods:

  1. milk.periodic.crate.order.report
     Dairy crate orders (from milk.dairy.order) aggregated by period.

  2. milk.periodic.dealer.crate.report
     Dealer-wise crate usage (from milk.crate.transaction) per period.

  3. milk.periodic.ledger.report
     Dealer transactions / payments / credit / crate balances per period.

Design rules followed:
  * TransientModel wizards — no new permanent tables that could break upgrades.
  * No existing model / field / workflow touched.
  * All SQL reads go through the ORM — no raw SQL.
  * Period helpers (_period_domain, _get_period_label) are reused across all three.
"""

import calendar
from datetime import date, timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

# ────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ────────────────────────────────────────────────────────────────────────────

_PERIOD_TYPES = [
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
    ('yearly', 'Yearly'),
]

_MONTH_NAMES = {
    1: 'January', 2: 'February', 3: 'March', 4: 'April',
    5: 'May', 6: 'June', 7: 'July', 8: 'August',
    9: 'September', 10: 'October', 11: 'November', 12: 'December',
}

_QUARTER_MONTHS = {
    1: (1, 3),  # Q1 → Jan–Mar
    2: (4, 6),  # Q2 → Apr–Jun
    3: (7, 9),  # Q3 → Jul–Sep
    4: (10, 12),  # Q4 → Oct–Dec
}


def _period_date_range(period_type, month, quarter, year):
    """
    Return (date_from, date_to) for the selected period.
    Raises ValidationError if the combination is invalid.
    """
    if period_type == 'monthly':
        m = int(month)
        last_day = calendar.monthrange(year, m)[1]
        return date(year, m, 1), date(year, m, last_day)

    if period_type == 'quarterly':
        q = int(quarter)
        start_m, end_m = _QUARTER_MONTHS[q]
        last_day = calendar.monthrange(year, end_m)[1]
        return date(year, start_m, 1), date(year, end_m, last_day)

    # yearly
    return date(year, 1, 1), date(year, 12, 31)


def _period_label(period_type, month, quarter, year):
    """Human-readable period label used in report headers."""
    if period_type == 'monthly':
        return f"{_MONTH_NAMES[int(month)]} {year}"
    if period_type == 'quarterly':
        return f"Q{quarter} {year}"
    return str(year)


# ════════════════════════════════════════════════════════════════════════════
# 1.  Dairy Crate Order Report (milk.periodic.crate.order.report)
# ════════════════════════════════════════════════════════════════════════════

class MilkPeriodicCrateOrderReport(models.TransientModel):
    """
    Aggregates dairy crate orders (milk.dairy.order) for a
    monthly / quarterly / yearly period.

    Shows each product, total ordered qty and total ordered amount.
    """
    _name = 'milk.periodic.crate.order.report'
    _description = 'Periodic Dairy Crate Order Report'

    # ── Period selector ──────────────────────────────────────────────────────
    period_type = fields.Selection(
        _PERIOD_TYPES, string='Period Type',
        required=True, default='monthly',
    )
    month = fields.Selection(
        [(str(i), _MONTH_NAMES[i]) for i in range(1, 13)],
        string='Month',
        default=lambda self: str(fields.Date.today().month),
    )
    quarter = fields.Selection(
        [('1', 'Q1 (Jan–Mar)'), ('2', 'Q2 (Apr–Jun)'),
         ('3', 'Q3 (Jul–Sep)'), ('4', 'Q4 (Oct–Dec)')],
        string='Quarter', default='1',
    )
    year = fields.Integer(
        string='Year',
        default=lambda self: fields.Date.today().year,
        required=True,
    )

    # ── Computed period label + dates ────────────────────────────────────────
    period_label = fields.Char(
        compute='_compute_period_label', string='Period',
    )
    date_from = fields.Date(compute='_compute_period_label', string='From')
    date_to = fields.Date(compute='_compute_period_label', string='To')

    @api.depends('period_type', 'month', 'quarter', 'year')
    def _compute_period_label(self):
        for rec in self:
            try:
                df, dt = _period_date_range(
                    rec.period_type, rec.month or '1',
                    int(rec.quarter or 1), rec.year or fields.Date.today().year,
                )
                rec.date_from = df
                rec.date_to = dt
                rec.period_label = _period_label(
                    rec.period_type, rec.month, int(rec.quarter or 1), rec.year,
                )
            except Exception:
                rec.date_from = rec.date_to = False
                rec.period_label = ''

    # ── Lines ────────────────────────────────────────────────────────────────
    line_ids = fields.One2many(
        'milk.periodic.crate.order.line', 'report_id', string='Lines',
    )
    total_ordered_qty = fields.Float(
        compute='_compute_totals', string='Total Qty (Crates)', digits=(16, 2),
    )
    total_ordered_amount = fields.Float(
        compute='_compute_totals', string='Total Amount (Rs)', digits=(16, 2),
    )
    order_count = fields.Integer(
        compute='_compute_totals', string='Dairy Orders',
    )

    @api.depends('line_ids.total_qty', 'line_ids.total_amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_ordered_qty = sum(rec.line_ids.mapped('total_qty'))
            rec.total_ordered_amount = sum(rec.line_ids.mapped('total_amount'))
            rec.order_count = sum(rec.line_ids.mapped('order_count'))

    # ── Generate ─────────────────────────────────────────────────────────────

    @api.constrains('year')
    def _check_year(self):
        for rec in self:
            if rec.year and (rec.year < 2000 or rec.year > 2100):
                raise ValidationError("Year must be between 2000 and 2100.")

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        df, dt = _period_date_range(
            self.period_type, self.month or '1',
            int(self.quarter or 1), self.year,
        )

        orders = self.env['milk.dairy.order'].search([
            ('date', '>=', df),
            ('date', '<=', dt),
            ('state', 'in', ('sent', 'fulfilled')),
        ])

        if not orders:
            raise UserError(
                f"No sent/fulfilled Dairy Orders found for {self.period_label}.\n\n"
                "Create and send Dairy Orders for this period first."
            )

        # Aggregate by product
        product_data = {}  # product_id -> {qty, amount, order_ids}
        for order in orders:
            for line in order.line_ids:
                pid = line.product_id.id
                if pid not in product_data:
                    product_data[pid] = {
                        'product_id': pid,
                        'total_qty': 0.0,
                        'total_amount': 0.0,
                        'order_ids': set(),
                    }
                product_data[pid]['total_qty'] += line.ordered_qty
                product_data[pid]['total_amount'] += line.ordered_amount
                product_data[pid]['order_ids'].add(order.id)

        new_lines = [
            {
                'report_id': self.id,
                'product_id': data['product_id'],
                'total_qty': data['total_qty'],
                'total_amount': data['total_amount'],
                'order_count': len(data['order_ids']),
            }
            for data in sorted(
                product_data.values(),
                key=lambda d: d['total_amount'],
                reverse=True,
            )
        ]
        if new_lines:
            self.env['milk.periodic.crate.order.line'].create(new_lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.periodic.crate.order.report',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError("Generate the report first before printing.")
        return self.env.ref(
            'milk_distribution_management.action_report_periodic_crate_order'
        ).report_action(self)


class MilkPeriodicCrateOrderLine(models.TransientModel):
    _name = 'milk.periodic.crate.order.line'
    _description = 'Periodic Crate Order Line'
    _order = 'total_amount desc'

    report_id = fields.Many2one('milk.periodic.crate.order.report', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product')
    total_qty = fields.Float(string='Total Ordered (Crates)', digits=(16, 2))
    total_amount = fields.Float(string='Total Amount (Rs)', digits=(16, 2))
    order_count = fields.Integer(string='Orders')


# ════════════════════════════════════════════════════════════════════════════
# 2.  Dealer-wise Crate Usage Report (milk.periodic.dealer.crate.report)
# ════════════════════════════════════════════════════════════════════════════

class MilkPeriodicDealerCrateReport(models.TransientModel):
    """
    Dealer-wise crate usage per period.

    For each dealer shows:
      opening crates (balance at start of period)
      total issued in period
      total returned in period
      closing crates (balance at end of period)
      net crates still with dealer
    """
    _name = 'milk.periodic.dealer.crate.report'
    _description = 'Periodic Dealer-wise Crate Usage Report'

    period_type = fields.Selection(_PERIOD_TYPES, string='Period Type', required=True, default='monthly')
    month = fields.Selection([(str(i), _MONTH_NAMES[i]) for i in range(1, 13)], string='Month',
                             default=lambda self: str(fields.Date.today().month))
    quarter = fields.Selection(
        [('1', 'Q1 (Jan–Mar)'), ('2', 'Q2 (Apr–Jun)'),
         ('3', 'Q3 (Jul–Sep)'), ('4', 'Q4 (Oct–Dec)')],
        string='Quarter', default='1')
    year = fields.Integer(string='Year', required=True,
                          default=lambda self: fields.Date.today().year)

    period_label = fields.Char(compute='_compute_period_label', string='Period')
    date_from = fields.Date(compute='_compute_period_label', string='From')
    date_to = fields.Date(compute='_compute_period_label', string='To')

    @api.depends('period_type', 'month', 'quarter', 'year')
    def _compute_period_label(self):
        for rec in self:
            try:
                df, dt = _period_date_range(
                    rec.period_type, rec.month or '1',
                    int(rec.quarter or 1), rec.year or fields.Date.today().year,
                )
                rec.date_from = df
                rec.date_to = dt
                rec.period_label = _period_label(
                    rec.period_type, rec.month, int(rec.quarter or 1), rec.year,
                )
            except Exception:
                rec.date_from = rec.date_to = False
                rec.period_label = ''

    line_ids = fields.One2many('milk.periodic.dealer.crate.line', 'report_id', string='Lines')
    total_issued = fields.Integer(compute='_compute_totals', string='Total Issued')
    total_returned = fields.Integer(compute='_compute_totals', string='Total Returned')
    total_net_crates = fields.Integer(compute='_compute_totals', string='Total Net Crates')

    @api.depends('line_ids.issued', 'line_ids.returned', 'line_ids.net_crates')
    def _compute_totals(self):
        for rec in self:
            rec.total_issued = sum(rec.line_ids.mapped('issued'))
            rec.total_returned = sum(rec.line_ids.mapped('returned'))
            rec.total_net_crates = sum(rec.line_ids.mapped('net_crates'))

    @api.constrains('year')
    def _check_year(self):
        for rec in self:
            if rec.year and (rec.year < 2000 or rec.year > 2100):
                raise ValidationError("Year must be between 2000 and 2100.")

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        df, dt = _period_date_range(
            self.period_type, self.month or '1',
            int(self.quarter or 1), self.year,
        )

        CrateTxn = self.env['milk.crate.transaction']

        # All confirmed transactions within the period
        txns = CrateTxn.search([
            ('state', '=', 'confirmed'),
            ('date', '>=', df),
            ('date', '<=', dt),
        ])

        if not txns:
            raise UserError(
                f"No confirmed crate transactions found for {self.period_label}.\n\n"
                "Confirm crate issue/return transactions for this period first."
            )

        # Aggregate by partner
        partner_data = {}  # partner_id -> {issued, returned}
        for t in txns:
            pid = t.partner_id.id
            if pid not in partner_data:
                partner_data[pid] = {'partner_id': pid, 'issued': 0, 'returned': 0}
            if t.move_type == 'issue':
                partner_data[pid]['issued'] += t.qty
            else:
                partner_data[pid]['returned'] += t.qty

        new_lines = []
        for data in partner_data.values():
            pid = data['partner_id']

            # Opening = balance_after of the last confirmed txn BEFORE df
            last_before = CrateTxn.search([
                ('partner_id', '=', pid),
                ('state', '=', 'confirmed'),
                ('date', '<', df),
            ], order='date desc, id desc', limit=1)
            opening = last_before.balance_after if last_before else 0

            new_lines.append({
                'report_id': self.id,
                'partner_id': pid,
                'opening': opening,
                'issued': data['issued'],
                'returned': data['returned'],
            })

        # Sort by dealer name
        new_lines.sort(key=lambda l: self.env['res.partner'].browse(l['partner_id']).name or '')

        if new_lines:
            self.env['milk.periodic.dealer.crate.line'].create(new_lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.periodic.dealer.crate.report',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError("Generate the report first before printing.")
        return self.env.ref(
            'milk_distribution_management.action_report_periodic_dealer_crate'
        ).report_action(self)


class MilkPeriodicDealerCrateLine(models.TransientModel):
    _name = 'milk.periodic.dealer.crate.line'
    _description = 'Periodic Dealer Crate Line'
    _order = 'partner_id'

    report_id = fields.Many2one('milk.periodic.dealer.crate.report', ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Dealer')
    opening = fields.Integer(string='Opening Crates')
    issued = fields.Integer(string='Issued')
    returned = fields.Integer(string='Returned')
    closing = fields.Integer(string='Closing Crates', compute='_compute_closing', store=True)
    net_crates = fields.Integer(string='Net (Still with Dealer)', compute='_compute_closing', store=True)

    @api.depends('opening', 'issued', 'returned')
    def _compute_closing(self):
        for rec in self:
            rec.closing = rec.opening + rec.issued - rec.returned
            rec.net_crates = rec.closing  # alias for template clarity


# ════════════════════════════════════════════════════════════════════════════
# 3.  Periodic Ledger Report (milk.periodic.ledger.report)
#     Transactions / Payments / Credit / Crate Balances per dealer per period
# ════════════════════════════════════════════════════════════════════════════

class MilkPeriodicLedgerReport(models.TransientModel):
    """
    Dealer-level summary for a monthly / quarterly / yearly period.

    For each dealer shows:
      opening balance (cash ledger)
      total billed
      total paid/received
      closing balance (cash)
      opening crates
      total crates issued
      total crates returned
      closing crates
    """
    _name = 'milk.periodic.ledger.report'
    _description = 'Periodic Ledger Report (Transactions / Payments / Credit / Crates)'

    period_type = fields.Selection(_PERIOD_TYPES, string='Period Type', required=True, default='monthly')
    month = fields.Selection([(str(i), _MONTH_NAMES[i]) for i in range(1, 13)], string='Month',
                             default=lambda self: str(fields.Date.today().month))
    quarter = fields.Selection(
        [('1', 'Q1 (Jan–Mar)'), ('2', 'Q2 (Apr–Jun)'),
         ('3', 'Q3 (Jul–Sep)'), ('4', 'Q4 (Oct–Dec)')],
        string='Quarter', default='1')
    year = fields.Integer(string='Year', required=True,
                          default=lambda self: fields.Date.today().year)

    period_label = fields.Char(compute='_compute_period_label', string='Period')
    date_from = fields.Date(compute='_compute_period_label', string='From')
    date_to = fields.Date(compute='_compute_period_label', string='To')

    @api.depends('period_type', 'month', 'quarter', 'year')
    def _compute_period_label(self):
        for rec in self:
            try:
                df, dt = _period_date_range(
                    rec.period_type, rec.month or '1',
                    int(rec.quarter or 1), rec.year or fields.Date.today().year,
                )
                rec.date_from = df
                rec.date_to = dt
                rec.period_label = _period_label(
                    rec.period_type, rec.month, int(rec.quarter or 1), rec.year,
                )
            except Exception:
                rec.date_from = rec.date_to = False
                rec.period_label = ''

    line_ids = fields.One2many('milk.periodic.ledger.line', 'report_id', string='Lines')

    # ── Grand totals ─────────────────────────────────────────────────────────
    grand_total_billed = fields.Float(compute='_compute_totals', string='Total Billed (Rs)', digits=(16, 2))
    grand_total_paid = fields.Float(compute='_compute_totals', string='Total Paid (Rs)', digits=(16, 2))
    grand_closing_cash = fields.Float(compute='_compute_totals', string='Closing Balance (Rs)', digits=(16, 2))
    grand_issued_crates = fields.Integer(compute='_compute_totals', string='Total Issued Crates')
    grand_returned_crates = fields.Integer(compute='_compute_totals', string='Total Returned Crates')
    grand_closing_crates = fields.Integer(compute='_compute_totals', string='Net Crates')

    @api.depends('line_ids.total_billed', 'line_ids.total_paid', 'line_ids.closing_balance',
                 'line_ids.issued_crates', 'line_ids.returned_crates', 'line_ids.closing_crates')
    def _compute_totals(self):
        for rec in self:
            rec.grand_total_billed = sum(rec.line_ids.mapped('total_billed'))
            rec.grand_total_paid = sum(rec.line_ids.mapped('total_paid'))
            rec.grand_closing_cash = sum(rec.line_ids.mapped('closing_balance'))
            rec.grand_issued_crates = sum(rec.line_ids.mapped('issued_crates'))
            rec.grand_returned_crates = sum(rec.line_ids.mapped('returned_crates'))
            rec.grand_closing_crates = sum(rec.line_ids.mapped('closing_crates'))

    @api.constrains('year')
    def _check_year(self):
        for rec in self:
            if rec.year and (rec.year < 2000 or rec.year > 2100):
                raise ValidationError("Year must be between 2000 and 2100.")

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        df, dt = _period_date_range(
            self.period_type, self.month or '1',
            int(self.quarter or 1), self.year,
        )

        Ledger = self.env['milk.partner.ledger']
        CrateTxn = self.env['milk.crate.transaction']

        # ── Cash ledger in period ─────────────────────────────────────────────
        ledger_rows = Ledger.search([
            ('date', '>=', df),
            ('date', '<=', dt),
        ])
        # Unique partners with activity in the period
        partner_ids = set(ledger_rows.mapped('partner_id').ids)

        # ── Crate txns in period (confirmed only) ─────────────────────────────
        crate_txns = CrateTxn.search([
            ('state', '=', 'confirmed'),
            ('date', '>=', df),
            ('date', '<=', dt),
        ])
        crate_partners = set(crate_txns.mapped('partner_id').ids)
        all_partners = partner_ids | crate_partners

        if not all_partners:
            raise UserError(
                f"No ledger or crate transactions found for {self.period_label}.\n\n"
                "Ensure dispatch sheets have been confirmed for this period."
            )

        new_lines = []
        for pid in sorted(all_partners):
            partner = self.env['res.partner'].browse(pid)

            # ── Cash ledger ───────────────────────────────────────────────────
            last_before_cash = Ledger.search([
                ('partner_id', '=', pid),
                ('date', '<', df),
            ], order='date desc', limit=1)
            opening_cash = last_before_cash.closing_balance if last_before_cash else 0.0

            p_ledger = ledger_rows.filtered(lambda l: l.partner_id.id == pid)
            total_billed = sum(p_ledger.mapped('today_bill'))
            total_paid = sum(p_ledger.mapped('received_amount'))
            closing_cash = opening_cash + total_billed - total_paid

            # ── Crate ledger ──────────────────────────────────────────────────
            last_before_crate = CrateTxn.search([
                ('partner_id', '=', pid),
                ('state', '=', 'confirmed'),
                ('date', '<', df),
            ], order='date desc, id desc', limit=1)
            opening_crates = last_before_crate.balance_after if last_before_crate else 0

            p_crates = crate_txns.filtered(lambda t: t.partner_id.id == pid)
            issued_crates = sum(t.qty for t in p_crates if t.move_type == 'issue')
            returned_crates = sum(t.qty for t in p_crates if t.move_type == 'return')
            closing_crates = opening_crates + issued_crates - returned_crates

            new_lines.append({
                'report_id': self.id,
                'partner_id': pid,
                'opening_balance': opening_cash,
                'total_billed': total_billed,
                'total_paid': total_paid,
                'closing_balance': closing_cash,
                'opening_crates': opening_crates,
                'issued_crates': issued_crates,
                'returned_crates': returned_crates,
                'closing_crates': closing_crates,
            })

        if new_lines:
            self.env['milk.periodic.ledger.line'].create(new_lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.periodic.ledger.report',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError("Generate the report first before printing.")
        return self.env.ref(
            'milk_distribution_management.action_report_periodic_ledger'
        ).report_action(self)


class MilkPeriodicLedgerLine(models.TransientModel):
    _name = 'milk.periodic.ledger.line'
    _description = 'Periodic Ledger Line'
    _order = 'partner_id'

    report_id = fields.Many2one('milk.periodic.ledger.report', ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Dealer')

    # Cash columns
    opening_balance = fields.Float(string='Opening (Rs)', digits=(16, 2))
    total_billed = fields.Float(string='Total Billed (Rs)', digits=(16, 2))
    total_paid = fields.Float(string='Total Paid (Rs)', digits=(16, 2))
    closing_balance = fields.Float(string='Closing Balance (Rs)', digits=(16, 2))

    # Crate columns
    opening_crates = fields.Integer(string='Opening Crates')
    issued_crates = fields.Integer(string='Issued')
    returned_crates = fields.Integer(string='Returned')
    closing_crates = fields.Integer(string='Closing Crates')


# ════════════════════════════════════════════════════════════════════════════
# Abstract PDF models for QWeb
# ════════════════════════════════════════════════════════════════════════════

# NOTE: AbstractModel _name values must produce a PostgreSQL table name
# under 63 characters (Odoo converts dots to underscores for the table name).
# The two names below were shortened to stay within that limit.
# report_periodic_reports.xml template ids and ir.actions.report report_name
# fields must match these exact values.

class ReportPeriodicCrateOrder(models.AbstractModel):
    # table: report_milk_dist_mgmt_periodic_crate_order  (44 chars) ✓
    _name = 'report.milk_dist_mgmt.periodic_crate_order'
    _description = 'Periodic Crate Order PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.periodic.crate.order.report'].browse(docids)
        return {'docs': docs, 'company': self.env.company}


class ReportPeriodicDealerCrate(models.AbstractModel):
    # table: report_milk_dist_mgmt_periodic_dealer_crate  (46 chars) ✓
    _name = 'report.milk_dist_mgmt.periodic_dealer_crate'
    _description = 'Periodic Dealer Crate Usage PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.periodic.dealer.crate.report'].browse(docids)
        return {'docs': docs, 'company': self.env.company}


class ReportPeriodicLedger(models.AbstractModel):
    # table: report_milk_distribution_management_report_periodic_ledger  (61 chars) ✓
    _name = 'report.milk_distribution_management.report_periodic_ledger'
    _description = 'Periodic Ledger PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.periodic.ledger.report'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
