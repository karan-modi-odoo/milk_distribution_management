from odoo import models, fields, api


class MilkDispatchLine(models.Model):
    _name = 'milk.dispatch.line'
    _description = 'Milk Dispatch Line'

    sheet_id = fields.Many2one('milk.dispatch.sheet', string='Dispatch Sheet', ondelete='cascade')
    partner_id = fields.Many2one('res.partner', required=True, string='Dealer')
    product_line_ids = fields.One2many('milk.dispatch.product.line', 'dispatch_line_id', string='Products')
    total_amount = fields.Float(compute='_compute_total', string='Total Amount', digits=(16, 2), store=True)

    @api.depends('product_line_ids.amount')
    def _compute_total(self):
        for rec in self:
            rec.total_amount = sum(rec.product_line_ids.mapped('amount'))

    _sql_constraints = [
        ('unique_partner', 'unique(sheet_id,partner_id)', 'A dealer can appear only once per dispatch sheet.'),
    ]
