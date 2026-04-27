from odoo import models, fields, api
from odoo.exceptions import ValidationError


class MilkDispatchProductLine(models.Model):
    _name = 'milk.dispatch.product.line'
    _description = 'Milk Dispatch Product Line'

    dispatch_line_id = fields.Many2one('milk.dispatch.line', string='Dispatch Line', ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, string='Product')
    qty = fields.Float(string='Qty / Crates', digits=(16, 2))
    rate = fields.Float(string='Rate (Rs)', digits=(16, 2))
    amount = fields.Float(compute='_compute_amount', string='Amount (Rs)', digits=(16, 2), store=True)

    @api.onchange('product_id')
    def _onchange_product(self):
        if not self.product_id:
            return
        # Feature 3: Check dealer-specific rate first
        partner = self.dispatch_line_id.partner_id
        if partner:
            dealer_rate = self.env['milk.dealer.rate'].search([
                ('dealer_id', '=', partner.id),
                ('product_id', '=', self.product_id.id),
            ], limit=1)
            if dealer_rate:
                self.rate = dealer_rate.rate
                return
        # Fall back to standard product price
        self.rate = self.product_id.lst_price

    @api.depends('qty', 'rate')
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.qty * rec.rate

    @api.constrains('qty')
    def _check_qty(self):
        for rec in self:
            if rec.qty < 0:
                raise ValidationError("Negative quantity is not allowed.")

    _sql_constraints = [
        ('unique_product', 'unique(dispatch_line_id,product_id)', 'The same product cannot appear twice for a dealer.'),
    ]
