from odoo import models, fields, api


class MilkDealerStatement(models.TransientModel):
    """
    Feature 2: Dealer-wise Statement
    Full date-wise ledger history for a selected dealer.
    Like a bank statement — date, opening, bill, cash, closing.
    """
    _name = 'milk.dealer.statement'
    _description = 'Dealer Statement'

    partner_id = fields.Many2one('res.partner', required=True, string='Dealer')
    date_from = fields.Date(string='From Date')
    date_to = fields.Date(string='To Date')
    line_ids = fields.One2many('milk.dealer.statement.line', 'statement_id', string='Lines')

    total_billed = fields.Float(compute='_compute_totals', string='Total Billed', digits=(16, 2))
    total_paid = fields.Float(compute='_compute_totals', string='Total Paid', digits=(16, 2))
    closing_balance = fields.Float(compute='_compute_totals', string='Closing Balance', digits=(16, 2))

    @api.depends('line_ids.today_bill', 'line_ids.received_amount', 'line_ids.closing_balance')
    def _compute_totals(self):
        for rec in self:
            rec.total_billed = sum(rec.line_ids.mapped('today_bill'))
            rec.total_paid = sum(rec.line_ids.mapped('received_amount'))
            rec.closing_balance = rec.line_ids[-1].closing_balance if rec.line_ids else 0.0

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        domain = [('partner_id', '=', self.partner_id.id)]
        if self.date_from:
            domain.append(('date', '>=', self.date_from))
        if self.date_to:
            domain.append(('date', '<=', self.date_to))

        ledger = self.env['milk.partner.ledger'].search(domain, order='date asc')
        lines = []
        for entry in ledger:
            lines.append({
                'statement_id': self.id,
                'date': entry.date,
                'opening_balance': entry.opening_balance,
                'today_bill': entry.today_bill,
                'received_amount': entry.received_amount,
                'closing_balance': entry.opening_balance + entry.today_bill - entry.received_amount,
            })

        if lines:
            self.env['milk.dealer.statement.line'].create(lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.dealer.statement',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'milk_distribution_management.action_report_dealer_statement'
        ).report_action(self)


class MilkDealerStatementLine(models.TransientModel):
    _name = 'milk.dealer.statement.line'
    _description = 'Dealer Statement Line'
    _order = 'date asc'

    statement_id = fields.Many2one('milk.dealer.statement', ondelete='cascade')
    date = fields.Date(string='Date')
    opening_balance = fields.Float(string='Opening (Rs)', digits=(16, 2))
    today_bill = fields.Float(string="Bill (Rs)", digits=(16, 2))
    received_amount = fields.Float(string='Cash (Rs)', digits=(16, 2))
    closing_balance = fields.Float(string='Closing (Rs)', digits=(16, 2))


# ── Abstract model for QWeb PDF ──────────────────────────────────────────────
class MilkDealerStatementPDF(models.AbstractModel):
    _name = 'report.milk_distribution_management.report_dealer_statement'
    _description = 'Dealer Statement PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.dealer.statement'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
