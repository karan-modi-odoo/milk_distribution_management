from odoo import models, fields


class MilkDealer(models.Model):
    """
    Extends res.partner with milk-distribution-specific fields:

    Role flags (Boolean):
      - milk_is_dealer     : partner acts as a milk dealer (dispatch lines,
                             cash collection, statements, etc.)
      - milk_is_driver     : partner acts as a delivery driver.
      - milk_is_collector  : partner acts as a cash collector / cashier.

    Financial / operational settings:
      - milk_credit_limit     : maximum outstanding allowed per dealer.
      - crate_charge_per_day  : rental charge (Rs) per crate per day for
                                unreturned crates. Set to 0 to skip billing.
    """

    _inherit = 'res.partner'

    # ── Role flags ────────────────────────────────────────────────────────────

    milk_is_dealer = fields.Boolean(
        string='Is Milk Dealer',
        default=False,
        help='Mark this contact as a milk dealer. '
             'Only dealers appear in dealer selection fields across the module.',
    )
    milk_is_driver = fields.Boolean(
        string='Is Milk Driver',
        default=False,
        help='Mark this contact as a delivery driver. '
             'Only drivers appear in the Driver field on dispatch sheets.',
    )
    milk_is_collector = fields.Boolean(
        string='Is Cash Collector',
        default=False,
        help='Mark this contact as a cash collector / cashier. '
             'Only collectors appear in the Collector field on cash collections.',
    )

    # ── Financial / operational settings ─────────────────────────────────────

    milk_credit_limit = fields.Float(
        string='Credit Limit (Rs)',
        default=0.0,
        help='Maximum outstanding balance allowed for this dealer. '
             'Set to 0 to disable the credit limit check.',
    )
    crate_charge_per_day = fields.Float(
        string='Crate Charge / Day (Rs)',
        default=0.0,
        digits=(16, 2),
        help='Rental charge per crate per day for unreturned crates. '
             'Set to 0 to skip this dealer in crate billing.',
    )
