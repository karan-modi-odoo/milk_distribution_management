import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class MilkDealerDefaultOrder(models.Model):
    """
    Stores a dealer-wise default product/qty/rate template.

    One template per dealer per route.  When a user clicks
    "Fill from Default Orders" on a draft dispatch sheet, every
    active template whose route matches the sheet's route is used
    to pre-populate dealer lines — without touching any lines that
    were already added manually or via "Copy Last Sheet".

    Design constraints
    ------------------
    * Does NOT touch any existing dispatch_sheet logic or workflow.
    * "Copy Last Sheet" remains fully independent and unchanged.
    * A template is silently skipped when it would create a duplicate
      dealer line (sheet already has a line for that dealer).
    * Managers configure templates; regular users apply them.
    """

    _name = 'milk.dealer.default.order'
    _description = 'Dealer Default Order Template'
    _order = 'route_id, partner_id'
    _rec_name = 'display_name'
    _inherit = ['mail.thread']

    # ── Identity ──────────────────────────────────────────────────────────────

    partner_id = fields.Many2one(
        'res.partner',
        string='Dealer',
        required=True,
        ondelete='cascade',
        tracking=True,
        index=True,
        domain=[('milk_is_dealer', '=', True)],
    )
    route_id = fields.Many2one(
        'milk.route',
        string='Route',
        required=True,
        ondelete='cascade',
        tracking=True,
        index=True,
    )
    active = fields.Boolean(
        default=True,
        tracking=True,
        help='Inactive templates are ignored during auto-fill.',
    )

    # ── Lines ─────────────────────────────────────────────────────────────────

    line_ids = fields.One2many(
        'milk.dealer.default.order.line',
        'default_order_id',
        string='Default Products',
    )

    # ── Display ───────────────────────────────────────────────────────────────

    display_name = fields.Char(
        compute='_compute_display_name',
        store=True,
    )

    @api.depends('partner_id', 'route_id')
    def _compute_display_name(self):
        for rec in self:
            dealer = rec.partner_id.name or ''
            route = rec.route_id.name or ''
            rec.display_name = f"{dealer} / {route}" if dealer or route else 'New'

    # ── SQL constraint: one template per dealer+route ─────────────────────────

    @api.constrains('partner_id', 'route_id')
    def _check_unique_dealer_route(self):
        for rec in self:
            if self.search_count([
                ('partner_id', '=', rec.partner_id.id),
                ('route_id', '=', rec.route_id.id),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("A default order template already exists for this dealer and route.")

    # ── Validation ───────────────────────────────────────────────────────────

    @api.constrains('line_ids')
    def _check_lines(self):
        for rec in self:
            if rec.line_ids and not all(
                    line.qty > 0 for line in rec.line_ids
            ):
                raise ValidationError(
                    "All default order lines must have a quantity greater than zero."
                )


class MilkDealerDefaultOrderLine(models.Model):
    """One product row inside a dealer default order template."""

    _name = 'milk.dealer.default.order.line'
    _description = 'Dealer Default Order Line'

    default_order_id = fields.Many2one(
        'milk.dealer.default.order',
        string='Default Order',
        ondelete='cascade',
        required=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
        ondelete='restrict',
    )
    # ── Pricing mode (read from product for display in the list) ─────────────
    is_piece_based = fields.Boolean(
        related='product_id.product_tmpl_id.milk_is_piece_based',
        string='Piece-Based',
        store=False,
        help='Mirrors the Piece-Based Item flag from the product. '
             'When True, qty = pieces and rate = per piece.',
    )
    qty = fields.Float(
        string='Default Qty',
        digits=(16, 2),
        required=True,
        default=1.0,
        help='Crate-based products: quantity in crates. '
             'Piece-based products: number of individual pieces.',
    )
    rate = fields.Float(
        string='Rate (Rs)',
        digits=(16, 2),
        help='Crate-based products: rate per crate. '
             'Piece-based products: rate per piece.',
    )

    # ── Onchange: mirror the rate-resolution logic from dispatch product line ──

    @api.onchange('product_id')
    def _onchange_product(self):
        """
        Resolve default rate using the same priority as MilkDispatchProductLine:
        1. milk_rate_per_crate on the product template (if configured)
        2. Standard sales price (lst_price) as fallback
        """
        if not self.product_id:
            return
        tmpl = self.product_id.product_tmpl_id
        if tmpl.milk_rate_per_crate and tmpl.milk_rate_per_crate > 0:
            self.rate = tmpl.milk_rate_per_crate
        else:
            self.rate = self.product_id.lst_price

    # ── Constraints ───────────────────────────────────────────────────────────

    @api.constrains('qty', 'rate')
    def _check_values(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError(
                    f"Default quantity must be greater than zero "
                    f"(product: {rec.product_id.name})."
                )
            if rec.rate < 0:
                raise ValidationError(
                    f"Rate cannot be negative "
                    f"(product: {rec.product_id.name})."
                )

    @api.constrains('default_order_id', 'product_id')
    def _check_unique_product_per_template(self):
        for rec in self:
            if self.search_count([
                ('default_order_id', '=', rec.default_order_id.id),
                ('product_id', '=', rec.product_id.id),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("The same product cannot appear twice in one default order template.")
