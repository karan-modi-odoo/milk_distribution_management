import calendar
from datetime import date
from odoo import models, fields, api


class MilkMonthlyClosing(models.TransientModel):
    """
    Feature 10: Monthly Closing Report
    For each dealer: opening of month, total billed, total paid, closing of month.
    Perfect for the accountant for income tax filing.
    """
    _name = 'milk.monthly.closing'
    _description = 'Monthly Closing Report'

    month = fields.Selection([
        ('1', 'January'), ('2', 'February'), ('3', 'March'),
        ('4', 'April'), ('5', 'May'), ('6', 'June'),
        ('7', 'July'), ('8', 'August'), ('9', 'September'),
        ('10', 'October'), ('11', 'November'), ('12', 'December'),
    ], required=True, default=lambda self: str(fields.Date.today().month), string='Month')
    year = fields.Integer(required=True, default=lambda self: fields.Date.today().year, string='Year')
    line_ids = fields.One2many('milk.monthly.closing.line', 'report_id', string='Lines')

    total_billed = fields.Float(compute='_compute_totals', string='Total Billed (Rs)', digits=(16, 2))
    total_paid = fields.Float(compute='_compute_totals', string='Total Paid (Rs)', digits=(16, 2))
    total_closing = fields.Float(compute='_compute_totals', string='Total Closing (Rs)', digits=(16, 2))

    @api.depends('line_ids.total_billed', 'line_ids.total_paid', 'line_ids.closing_balance')
    def _compute_totals(self):
        for rec in self:
            rec.total_billed = sum(rec.line_ids.mapped('total_billed'))
            rec.total_paid = sum(rec.line_ids.mapped('total_paid'))
            rec.total_closing = sum(rec.line_ids.mapped('closing_balance'))

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        month = int(self.month)
        year = self.year
        date_from = date(year, month, 1)
        date_to = date(year, month, calendar.monthrange(year, month)[1])

        ledger_in_month = self.env['milk.partner.ledger'].search([
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ])
        partner_ids = set(ledger_in_month.mapped('partner_id').ids)

        lines = []
        for partner_id in sorted(partner_ids):
            last_before = self.env['milk.partner.ledger'].search([
                ('partner_id', '=', partner_id),
                ('date', '<', date_from),
            ], order='date desc', limit=1)
            opening = last_before.closing_balance if last_before else 0.0

            month_entries = self.env['milk.partner.ledger'].search([
                ('partner_id', '=', partner_id),
                ('date', '>=', date_from),
                ('date', '<=', date_to),
            ])
            total_billed = sum(month_entries.mapped('today_bill'))
            total_paid = sum(month_entries.mapped('received_amount'))
            closing = opening + total_billed - total_paid

            lines.append({
                'report_id': self.id,
                'partner_id': partner_id,
                'opening_balance': opening,
                'total_billed': total_billed,
                'total_paid': total_paid,
                'closing_balance': closing,
            })

        if lines:
            self.env['milk.monthly.closing.line'].create(lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.monthly.closing',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'milk_distribution_management.action_report_monthly_closing'
        ).report_action(self)


class MilkMonthlyClosingLine(models.TransientModel):
    _name = 'milk.monthly.closing.line'
    _description = 'Monthly Closing Line'
    _order = 'partner_id'

    report_id = fields.Many2one('milk.monthly.closing', ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Dealer')
    opening_balance = fields.Float(string='Opening (Rs)', digits=(16, 2))
    total_billed = fields.Float(string='Total Billed (Rs)', digits=(16, 2))
    total_paid = fields.Float(string='Total Paid (Rs)', digits=(16, 2))
    closing_balance = fields.Float(string='Closing (Rs)', digits=(16, 2))


# ── Abstract model for QWeb PDF ──────────────────────────────────────────────
class MilkMonthlyClosingPDF(models.AbstractModel):
    _name = 'report.milk_distribution_management.report_monthly_closing'
    _description = 'Monthly Closing PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.monthly.closing'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
