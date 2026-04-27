from odoo import models, fields


class MilkDealerRate(models.Model):
    """
    Dealer-specific product rates.
    When a product is selected in a dispatch line, the system
    first checks here for a dealer-specific rate before
    falling back to the standard product price.
    """
    _name = 'milk.dealer.rate'
    _description = 'Dealer Product Rate'
    _rec_name = 'dealer_id'
    _order = 'dealer_id, product_id'

    dealer_id = fields.Many2one('res.partner', required=True, string='Dealer')
    product_id = fields.Many2one('product.product', required=True, string='Product')
    rate = fields.Float(string='Rate (Rs)', required=True, digits=(16, 2))

    _sql_constraints = [
        ('unique_dealer_product', 'unique(dealer_id, product_id)',
         'A rate for this dealer and product already exists.'),
    ]
