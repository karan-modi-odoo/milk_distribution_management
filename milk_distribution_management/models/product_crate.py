from odoo import models, fields

# ---------------------------------------------------------------------------
# Standard crate sizes used across product selection helper in the UI.
# The operator can override these by typing any positive integer.
# ---------------------------------------------------------------------------
CRATE_SIZE_PRESETS = [
    (24, '500 ml — 24 pcs/crate'),
    (12, '1 L — 12 pcs/crate'),
    (2, '6 L — 2 pcs/crate'),
    (6, 'Curd / 400g — 6 pcs/crate'),
]


class ProductTemplateMilkCrate(models.Model):
    """
    Extends product.template with milk-distribution crate / piece configuration.

    milk_is_piece_based:
        When True, the product is sold per individual piece (e.g. Paneer slabs).
        Qty on dispatch lines represents pieces; rate is per piece.
        All crate logic (pieces_per_crate, half-crate validation, crate
        auto-issue) is bypassed entirely for this product.
        Mutually exclusive with crate-based config in the UI.

    milk_pieces_per_crate:
        How many individual pieces make one full crate. Ignored when
        milk_is_piece_based is True.

    milk_allow_half_crate:
        Irrelevant when milk_pieces_per_crate = 0 or milk_is_piece_based = True.

    milk_rate_per_crate:
        Default billing rate per full crate (crate-based) or per piece
        (piece-based). Copied to the dispatch line on product selection.
    """
    _inherit = 'product.template'

    milk_is_piece_based = fields.Boolean(
        string='Piece-Based Item',
        default=False,
        help=(
            'Enable for products sold per individual piece '
            '(e.g. Paneer, Shrikhand slabs).\n\n'
            'When enabled:\n'
            '  • Qty on dispatch lines = number of pieces\n'
            '  • Rate = charged per piece\n'
            '  • Crate tracking and validation are fully bypassed\n\n'
            'Do not configure Pieces per Crate when this flag is enabled.'
        ),
    )
    milk_pieces_per_crate = fields.Integer(
        string='Pieces per Crate',
        default=0,
        help=(
            'Number of individual pieces in one full crate.\n\n'
            'Common values:\n'
            '  • 500 ml pouches  → 24\n'
            '  • 1 L pouches     → 12\n'
            '  • 6 L jars        →  2\n'
            '  • Curd / 400 g    →  6\n\n'
            'Set to 0 to disable crate-based pricing for this product.\n'
            'Ignored when Piece-Based Item is enabled.'
        ),
    )
    milk_allow_half_crate = fields.Boolean(
        string='Allow Half-Crate',
        default=True,
        help=(
            'When enabled, the dispatch line accepts quantities like '
            '0.5, 1.5, 2.5 crates (half-crate steps). '
            'Disable to enforce whole-crate quantities only. '
            'Irrelevant when Piece-Based Item is enabled.'
        ),
    )
    milk_rate_per_crate = fields.Float(
        string='Default Rate (Rs)',
        digits=(16, 2),
        default=0.0,
        help=(
            'Default billing rate per full crate (crate-based products) '
            'or per piece (piece-based products). '
            'Copied to the dispatch line on product selection. '
            'Dealer-specific rates (Configuration → Dealer Rates) '
            'always take precedence over this value.'
        ),
    )
