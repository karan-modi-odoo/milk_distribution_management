from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class MilkPaymentModeReport(models.TransientModel):
    """
    Payment Collection Mode-wise Report.

    Section 1 — Mode Summary (date range):
        One row per payment mode showing transaction count and total collected.

    Section 2 — Daily Reconciliation:
        One row per date showing each mode's collection, total bank deposited
        on that date, and cash-in-hand (cash collected minus bank deposited).

    Data sources (read-only):
        milk.cash.collection      state='confirmed', date in range
        milk.cash.collection.line payment_mode + collected_amount
        milk.bank.deposit         state='confirmed', date in range → amount
    """
    _name = 'milk.payment.mode.report'
    _description = 'Payment Mode-wise Collection Report'

    date_from = fields.Date(
        string='From Date', required=True, default=fields.Date.today,
    )
    date_to = fields.Date(
        string='To Date', required=True, default=fields.Date.today,
    )

    # ── Section 1: mode summary ───────────────────────────────────────────────
    line_ids = fields.One2many(
        'milk.payment.mode.report.line', 'report_id', string='Mode Summary',
    )

    # ── Section 2: daily reconciliation ──────────────────────────────────────
    daily_ids = fields.One2many(
        'milk.payment.mode.daily.line', 'report_id', string='Daily Reconciliation',
    )

    # ── Grand totals (computed from lines) ───────────────────────────────────
    total_cash = fields.Float(
        compute='_compute_grand_totals', string='Cash Total (Rs)', digits=(16, 2),
    )
    total_upi = fields.Float(
        compute='_compute_grand_totals', string='UPI Total (Rs)', digits=(16, 2),
    )
    total_cheque = fields.Float(
        compute='_compute_grand_totals', string='Cheque Total (Rs)', digits=(16, 2),
    )
    total_neft = fields.Float(
        compute='_compute_grand_totals', string='NEFT/RTGS Total (Rs)', digits=(16, 2),
    )
    grand_total = fields.Float(
        compute='_compute_grand_totals', string='Grand Total (Rs)', digits=(16, 2),
    )
    total_deposited = fields.Float(
        compute='_compute_grand_totals', string='Total Bank Deposited (Rs)', digits=(16, 2),
    )
    total_cash_in_hand = fields.Float(
        compute='_compute_grand_totals', string='Total Cash in Hand (Rs)', digits=(16, 2),
    )

    @api.depends(
        'line_ids.total_amount',
        'daily_ids.bank_deposited',
        'daily_ids.cash_in_hand',
    )
    def _compute_grand_totals(self):
        for rec in self:
            mode_map = {
                l.payment_mode: l.total_amount for l in rec.line_ids
            }
            rec.total_cash = mode_map.get('cash', 0.0)
            rec.total_upi = mode_map.get('upi', 0.0)
            rec.total_cheque = mode_map.get('cheque', 0.0)
            rec.total_neft = mode_map.get('neft', 0.0)
            rec.grand_total = sum(mode_map.values())
            rec.total_deposited = sum(rec.daily_ids.mapped('bank_deposited'))
            rec.total_cash_in_hand = sum(rec.daily_ids.mapped('cash_in_hand'))

    # ── Constraints ───────────────────────────────────────────────────────────

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from > rec.date_to:
                raise ValidationError("'From Date' must be on or before 'To Date'.")

    # ── Generate ──────────────────────────────────────────────────────────────

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()
        self.daily_ids.unlink()

        # Confirmed collections in range
        collections = self.env['milk.cash.collection'].search([
            ('state', '=', 'confirmed'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ])

        if not collections:
            raise UserError(
                "No confirmed cash collections found for the selected date range."
            )

        # ── Aggregate all collection lines ───────────────────────────────────
        # mode_data  : {mode: {count, amount}}
        # daily_data : {date: {mode: amount, 'bank_deposited': float}}
        mode_data = {}
        daily_data = {}

        for col in collections:
            date_key = col.date
            if date_key not in daily_data:
                daily_data[date_key] = {
                    'cash': 0.0, 'upi': 0.0,
                    'cheque': 0.0, 'neft': 0.0,
                    'bank_deposited': 0.0,
                }
            for line in col.line_ids:
                if not line.collected_amount:
                    continue
                mode = line.payment_mode
                # mode summary
                if mode not in mode_data:
                    mode_data[mode] = {'txn_count': 0, 'total_amount': 0.0}
                mode_data[mode]['txn_count'] += 1
                mode_data[mode]['total_amount'] += line.collected_amount
                # daily breakdown
                if mode in daily_data[date_key]:
                    daily_data[date_key][mode] += line.collected_amount

        # ── Bank deposits per date ────────────────────────────────────────────
        deposits = self.env['milk.bank.deposit'].search([
            ('state', '=', 'confirmed'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ])
        for dep in deposits:
            if dep.date not in daily_data:
                daily_data[dep.date] = {
                    'cash': 0.0, 'upi': 0.0,
                    'cheque': 0.0, 'neft': 0.0,
                    'bank_deposited': 0.0,
                }
            daily_data[dep.date]['bank_deposited'] += dep.amount

        # ── Create mode summary lines ─────────────────────────────────────────
        _MODE_ORDER = ['cash', 'upi', 'cheque', 'neft']
        mode_lines = []
        for mode in _MODE_ORDER:
            if mode in mode_data:
                mode_lines.append({
                    'report_id': self.id,
                    'payment_mode': mode,
                    'txn_count': mode_data[mode]['txn_count'],
                    'total_amount': mode_data[mode]['total_amount'],
                })
        if mode_lines:
            self.env['milk.payment.mode.report.line'].create(mode_lines)

        # ── Create daily reconciliation lines ─────────────────────────────────
        daily_lines = []
        for date_key in sorted(daily_data.keys()):
            d = daily_data[date_key]
            total_collected = d['cash'] + d['upi'] + d['cheque'] + d['neft']
            # cash_in_hand = cash collected that day minus bank deposited that day
            cash_in_hand = d['cash'] - d['bank_deposited']
            daily_lines.append({
                'report_id': self.id,
                'date': date_key,
                'cash_collected': d['cash'],
                'upi_collected': d['upi'],
                'cheque_collected': d['cheque'],
                'neft_collected': d['neft'],
                'total_collected': total_collected,
                'bank_deposited': d['bank_deposited'],
                'cash_in_hand': cash_in_hand,
            })
        if daily_lines:
            self.env['milk.payment.mode.daily.line'].create(daily_lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.payment.mode.report',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError("Generate the report first before printing.")
        return self.env.ref(
            'milk_distribution_management.action_report_payment_mode'
        ).report_action(self)


class MilkPaymentModeReportLine(models.TransientModel):
    """One row per payment mode — summary for the selected date range."""
    _name = 'milk.payment.mode.report.line'
    _description = 'Payment Mode Report Line'
    _order = 'payment_mode'

    report_id = fields.Many2one('milk.payment.mode.report', ondelete='cascade')
    payment_mode = fields.Selection([
        ('cash', 'Cash'),
        ('upi', 'UPI'),
        ('cheque', 'Cheque'),
        ('neft', 'NEFT / RTGS'),
    ], string='Payment Mode')
    txn_count = fields.Integer(string='Transactions')
    total_amount = fields.Float(string='Total Collected (Rs)', digits=(16, 2))


class MilkPaymentModeDailyLine(models.TransientModel):
    """One row per date — breakdown by mode + bank deposit reconciliation."""
    _name = 'milk.payment.mode.daily.line'
    _description = 'Payment Mode Daily Reconciliation Line'
    _order = 'date asc'

    report_id = fields.Many2one('milk.payment.mode.report', ondelete='cascade')
    date = fields.Date(string='Date')
    cash_collected = fields.Float(string='Cash (Rs)', digits=(16, 2))
    upi_collected = fields.Float(string='UPI (Rs)', digits=(16, 2))
    cheque_collected = fields.Float(string='Cheque (Rs)', digits=(16, 2))
    neft_collected = fields.Float(string='NEFT/RTGS (Rs)', digits=(16, 2))
    total_collected = fields.Float(string='Total Collected (Rs)', digits=(16, 2))
    bank_deposited = fields.Float(
        string='Bank Deposited (Rs)', digits=(16, 2),
        help='Sum of confirmed bank deposits on this date.',
    )
    cash_in_hand = fields.Float(
        string='Cash in Hand (Rs)', digits=(16, 2),
        help='Cash collected on this date minus bank deposited on this date.',
    )


# ── Abstract model for QWeb PDF ───────────────────────────────────────────────
class ReportPaymentMode(models.AbstractModel):
    _name = 'report.milk_distribution_management.report_payment_mode'
    _description = 'Payment Mode Report PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.payment.mode.report'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
