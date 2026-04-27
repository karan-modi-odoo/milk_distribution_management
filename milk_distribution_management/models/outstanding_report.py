from odoo import models, fields, api


class MilkOutstandingReport(models.TransientModel):
    """
    Feature 1: Outstanding Report
    Shows current outstanding balance for ALL dealers in one PDF.
    Useful for income tax and accountant review.
    """
    _name = 'milk.outstanding.report'
    _description = 'Outstanding Report'

    line_ids = fields.One2many('milk.outstanding.report.line', 'report_id', string='Lines')
    total_outstanding = fields.Float(compute='_compute_total', string='Total Outstanding', digits=(16, 2))

    @api.depends('line_ids.outstanding')
    def _compute_total(self):
        for rec in self:
            rec.total_outstanding = sum(rec.line_ids.mapped('outstanding'))

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        # Get latest ledger entry per partner
        all_ledger = self.env['milk.partner.ledger'].search([], order='partner_id, date desc')
        seen = set()
        lines = []
        for entry in all_ledger:
            pid = entry.partner_id.id
            if pid in seen:
                continue
            seen.add(pid)
            if entry.closing_balance > 0:
                lines.append({
                    'report_id': self.id,
                    'partner_id': pid,
                    'last_date': entry.date,
                    'outstanding': entry.closing_balance,
                })

        # Sort by outstanding descending
        lines.sort(key=lambda x: x['outstanding'], reverse=True)
        if lines:
            self.env['milk.outstanding.report.line'].create(lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.outstanding.report',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'milk_distribution_management.action_report_outstanding'
        ).report_action(self)


class MilkOutstandingReportLine(models.TransientModel):
    _name = 'milk.outstanding.report.line'
    _description = 'Outstanding Report Line'

    report_id = fields.Many2one('milk.outstanding.report', ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Dealer')
    last_date = fields.Date(string='Last Bill Date')
    outstanding = fields.Float(string='Outstanding (Rs)', digits=(16, 2))


# ── Abstract model for QWeb PDF ──────────────────────────────────────────────
class MilkOutstandingReportPDF(models.AbstractModel):
    _name = 'report.milk_distribution_management.report_outstanding'
    _description = 'Outstanding Report PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.outstanding.report'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
