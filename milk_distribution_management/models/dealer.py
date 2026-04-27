from odoo import models, fields


class MilkDealer(models.Model):
    """
    Extends res.partner with milk-specific fields:
      - milk_credit_limit: maximum outstanding allowed per dealer.
        Set to 0 for no limit.
    """
    _inherit = 'res.partner'

    milk_credit_limit = fields.Float(
        string='Credit Limit (Rs)',
        default=0.0,
        help='Maximum outstanding balance allowed for this dealer. '
             'Set to 0 to disable the credit limit check.',
    )
