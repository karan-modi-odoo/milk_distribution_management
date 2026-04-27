from odoo import models, fields, api


class MilkDailySummary(models.Model):
    _name = 'milk.daily.summary'
    _description = 'Milk Daily Summary'
    _order = 'date desc'
    _rec_name = 'date'

    date = fields.Date(required=True, default=fields.Date.today, string='Date')
    line_ids = fields.One2many('milk.daily.summary.line', 'summary_id', string='Dealer Rows')

    total_opening = fields.Float(compute='_compute_footer', string='Total Opening', digits=(16, 2))
    total_rashmi = fields.Float(compute='_compute_footer', string='Rashmi Total', digits=(16, 2))
    total_giriraj = fields.Float(compute='_compute_footer', string='Giriraj Total', digits=(16, 2))
    total_pm = fields.Float(compute='_compute_footer', string='PM Total', digits=(16, 2))
    total_today = fields.Float(compute='_compute_footer', string='Total Today', digits=(16, 2))
    total_cash = fields.Float(compute='_compute_footer', string='Total Cash', digits=(16, 2))
    total_closing = fields.Float(compute='_compute_footer', string='Total Closing', digits=(16, 2))

    @api.depends(
        'line_ids.opening_balance', 'line_ids.rashmi_amount',
        'line_ids.giriraj_amount', 'line_ids.pm_amount',
        'line_ids.total_today', 'line_ids.cash_received', 'line_ids.closing_balance',
    )
    def _compute_footer(self):
        for rec in self:
            rec.total_opening = sum(rec.line_ids.mapped('opening_balance'))
            rec.total_rashmi = sum(rec.line_ids.mapped('rashmi_amount'))
            rec.total_giriraj = sum(rec.line_ids.mapped('giriraj_amount'))
            rec.total_pm = sum(rec.line_ids.mapped('pm_amount'))
            rec.total_today = sum(rec.line_ids.mapped('total_today'))
            rec.total_cash = sum(rec.line_ids.mapped('cash_received'))
            rec.total_closing = sum(rec.line_ids.mapped('closing_balance'))

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        sheets = self.env['milk.dispatch.sheet'].search([
            ('date', '=', self.date),
            ('state', '=', 'confirmed'),
        ])

        partner_ids = set()
        for sheet in sheets:
            partner_ids.update(sheet.line_ids.mapped('partner_id').ids)

        lines_to_create = []
        for partner_id in sorted(partner_ids):
            rashmi = giriraj = pm = 0.0
            for sheet in sheets:
                dl = sheet.line_ids.filtered(lambda l, pid=partner_id: l.partner_id.id == pid)
                amount = sum(dl.mapped('total_amount'))
                if sheet.route == 'rashmi_am':
                    rashmi += amount
                elif sheet.route == 'giriraj_am':
                    giriraj += amount
                elif sheet.route == 'pm':
                    pm += amount

            ledger = self.env['milk.partner.ledger'].search([
                ('partner_id', '=', partner_id),
                ('date', '=', self.date),
            ], limit=1)

            lines_to_create.append({
                'summary_id': self.id,
                'partner_id': partner_id,
                'opening_balance': ledger.opening_balance if ledger else 0.0,
                'rashmi_amount': rashmi,
                'giriraj_amount': giriraj,
                'pm_amount': pm,
                'cash_received': ledger.received_amount if ledger else 0.0,
            })

        if lines_to_create:
            self.env['milk.daily.summary.line'].create(lines_to_create)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.daily.summary',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }


class MilkDailySummaryLine(models.Model):
    _name = 'milk.daily.summary.line'
    _description = 'Milk Daily Summary Line'
    _order = 'partner_id'

    summary_id = fields.Many2one('milk.daily.summary', ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Dealer', readonly=True)
    opening_balance = fields.Float(string='Opening (Rs)', digits=(16, 2), readonly=True)
    rashmi_amount = fields.Float(string='Rashmi AM (Rs)', digits=(16, 2), readonly=True)
    giriraj_amount = fields.Float(string='Giriraj AM (Rs)', digits=(16, 2), readonly=True)
    pm_amount = fields.Float(string='PM (Rs)', digits=(16, 2), readonly=True)
    cash_received = fields.Float(string='Cash (Rs)', digits=(16, 2), readonly=True)
    total_today = fields.Float(compute='_compute_totals', string='Total Today (Rs)', digits=(16, 2), store=True)
    closing_balance = fields.Float(compute='_compute_totals', string='Closing (Rs)', digits=(16, 2), store=True)

    @api.depends('opening_balance', 'rashmi_amount', 'giriraj_amount', 'pm_amount', 'cash_received')
    def _compute_totals(self):
        for rec in self:
            rec.total_today = rec.rashmi_amount + rec.giriraj_amount + rec.pm_amount
            rec.closing_balance = rec.opening_balance + rec.total_today - rec.cash_received
