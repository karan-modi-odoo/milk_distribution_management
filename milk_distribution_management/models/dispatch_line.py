from odoo import models, fields, api
from odoo.exceptions import ValidationError


class MilkDispatchLine(models.Model):
    _name = 'milk.dispatch.line'
    _description = 'Milk Dispatch Line'

    sheet_id = fields.Many2one('milk.dispatch.sheet', string='Dispatch Sheet', ondelete='cascade')
    partner_id = fields.Many2one(
        'res.partner',
        required=True,
        string='Dealer',
        domain=[('milk_is_dealer', '=', True)],
    )
    product_line_ids = fields.One2many('milk.dispatch.product.line', 'dispatch_line_id', string='Products')
    total_amount = fields.Float(compute='_compute_total', string='Total Amount', digits=(16, 2), store=True)

    @api.depends('product_line_ids.amount')
    def _compute_total(self):
        for rec in self:
            rec.total_amount = sum(rec.product_line_ids.mapped('amount'))

    @api.constrains('sheet_id', 'partner_id')
    def _check_unique_partner_per_sheet(self):
        for rec in self:
            if self.search_count([
                ('sheet_id', '=', rec.sheet_id.id),
                ('partner_id', '=', rec.partner_id.id),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("A dealer can appear only once per dispatch sheet.")
