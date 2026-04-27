from odoo import models, fields, api


class MilkDriverPerformance(models.TransientModel):
    """
    Feature 9: Driver Performance Report
    Total sheets, dealers covered, and delivery amount per driver.
    """
    _name = 'milk.driver.performance'
    _description = 'Driver Performance Report'

    date_from = fields.Date(required=True, default=fields.Date.today, string='From Date')
    date_to = fields.Date(required=True, default=fields.Date.today, string='To Date')
    line_ids = fields.One2many('milk.driver.performance.line', 'report_id', string='Lines')

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        sheets = self.env['milk.dispatch.sheet'].search([
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'confirmed'),
            ('driver_id', '!=', False),
        ])

        driver_data = {}
        for sheet in sheets:
            did = sheet.driver_id.id
            if did not in driver_data:
                driver_data[did] = {'sheets': 0, 'dealers': 0, 'amount': 0.0}
            driver_data[did]['sheets'] += 1
            driver_data[did]['dealers'] += len(sheet.line_ids)
            driver_data[did]['amount'] += sheet.total_amount

        lines = [
            {
                'report_id': self.id,
                'driver_id': did,
                'total_sheets': data['sheets'],
                'total_dealers': data['dealers'],
                'total_amount': data['amount'],
            }
            for did, data in driver_data.items()
        ]
        if lines:
            self.env['milk.driver.performance.line'].create(lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.driver.performance',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'milk_distribution_management.action_report_driver_performance'
        ).report_action(self)


class MilkDriverPerformanceLine(models.TransientModel):
    _name = 'milk.driver.performance.line'
    _description = 'Driver Performance Line'
    _order = 'total_amount desc'

    report_id = fields.Many2one('milk.driver.performance', ondelete='cascade')
    driver_id = fields.Many2one('res.partner', string='Driver')
    total_sheets = fields.Integer(string='Sheets')
    total_dealers = fields.Integer(string='Dealers Covered')
    total_amount = fields.Float(string='Total Amount (Rs)', digits=(16, 2))


# ── Abstract model for QWeb PDF ──────────────────────────────────────────────
class MilkDriverPerformancePDF(models.AbstractModel):
    _name = 'report.milk_distribution_management.report_driver_performance'
    _description = 'Driver Performance PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.driver.performance'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
