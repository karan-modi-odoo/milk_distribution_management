from odoo import models, fields, api


class MilkProductSalesReport(models.TransientModel):
    """
    Feature 8: Product-wise Sales Report
    How many crates of each product sold in a date range.
    Useful for ordering from Amul.
    """
    _name = 'milk.product.sales.report'
    _description = 'Product Sales Report'

    date_from = fields.Date(required=True, default=fields.Date.today, string='From Date')
    date_to = fields.Date(required=True, default=fields.Date.today, string='To Date')
    line_ids = fields.One2many('milk.product.sales.report.line', 'report_id', string='Lines')
    total_amount = fields.Float(compute='_compute_total', string='Grand Total (Rs)', digits=(16, 2))

    @api.depends('line_ids.total_amount')
    def _compute_total(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped('total_amount'))

    def action_generate(self):
        self.ensure_one()
        self.line_ids.unlink()

        sheets = self.env['milk.dispatch.sheet'].search([
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'confirmed'),
        ])

        product_data = {}
        for sheet in sheets:
            for dl in sheet.line_ids:
                for pl in dl.product_line_ids:
                    if pl.qty <= 0:
                        continue
                    pid = pl.product_id.id
                    if pid not in product_data:
                        product_data[pid] = {'qty': 0.0, 'amount': 0.0}
                    product_data[pid]['qty'] += pl.qty
                    product_data[pid]['amount'] += pl.amount

        lines = [
            {
                'report_id': self.id,
                'product_id': pid,
                'total_qty': data['qty'],
                'total_amount': data['amount'],
            }
            for pid, data in product_data.items()
        ]
        if lines:
            self.env['milk.product.sales.report.line'].create(lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.product.sales.report',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'milk_distribution_management.action_report_product_sales'
        ).report_action(self)


class MilkProductSalesReportLine(models.TransientModel):
    _name = 'milk.product.sales.report.line'
    _description = 'Product Sales Report Line'
    _order = 'total_amount desc'

    report_id = fields.Many2one('milk.product.sales.report', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product')
    total_qty = fields.Float(string='Total Qty / Crates', digits=(16, 2))
    total_amount = fields.Float(string='Total Amount (Rs)', digits=(16, 2))


# ── Abstract model for QWeb PDF ──────────────────────────────────────────────
class MilkProductSalesReportPDF(models.AbstractModel):
    _name = 'report.milk_distribution_management.report_product_sales'
    _description = 'Product Sales Report PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.product.sales.report'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
