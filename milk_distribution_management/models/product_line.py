from odoo import models, fields, api
from odoo.exceptions import ValidationError


class MilkDispatchProductLine(models.Model):
    _name = 'milk.dispatch.product.line'
    _description = 'Milk Dispatch Product Line'

    dispatch_line_id = fields.Many2one(
        'milk.dispatch.line', string='Dispatch Line', ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', required=True, string='Product',
    )

    # ── Pricing mode (read from product, stored for display & validation) ─────

    is_piece_based = fields.Boolean(
        string='Piece-Based',
        compute='_compute_crate_info',
        store=True,
        help="Mirrors the product's 'Piece-Based Item' flag. "
             "When True, qty = pieces and rate = per piece.",
    )

    # ── Crate configuration (read from product, stored for display) ───────────

    pieces_per_crate = fields.Integer(
        string='Pcs / Crate',
        compute='_compute_crate_info',
        store=True,
        help="Pulled from the product's 'Pieces per Crate' setting. "
             "0 means crate logic is disabled for this product. "
             "Always 0 for piece-based products.",
    )
    allow_half_crate = fields.Boolean(
        string='Half-Crate Allowed',
        compute='_compute_crate_info',
        store=True,
        help="Mirrors the product's 'Allow Half-Crate' flag. "
             "Irrelevant for piece-based products.",
    )

    # ── Quantity ─────────────────────────────────────────────────────────────

    qty = fields.Float(
        string='Qty',
        digits=(16, 2),
        help='Crate-based products: enter quantity in full crates '
             '(or half-crates if the product allows it, e.g. 1.5 = one and '
             'a half crates). '
             'Piece-based products: enter number of individual pieces.',
    )
    pieces_qty = fields.Float(
        string='Pieces',
        compute='_compute_pieces_qty',
        digits=(16, 0),
        help='Crate-based: Qty × Pieces per Crate. '
             'Piece-based: not applicable (always 0). '
             'Read-only.',
    )

    # ── Rate & Amount ─────────────────────────────────────────────────────────

    rate = fields.Float(
        string='Rate (Rs)', digits=(16, 2),
        help='Crate-based: rate per full crate. '
             'Piece-based: rate per individual piece.',
    )
    amount = fields.Float(
        compute='_compute_amount', string='Amount (Rs)',
        digits=(16, 2), store=True,
    )

    # ── Computed: pull crate / piece config from product ─────────────────────

    @api.depends('product_id')
    def _compute_crate_info(self):
        for rec in self:
            tmpl = rec.product_id.product_tmpl_id if rec.product_id else None
            if tmpl:
                rec.is_piece_based = tmpl.milk_is_piece_based
                # For piece-based products, crate fields are irrelevant;
                # store them as 0 / True to avoid confusion.
                if tmpl.milk_is_piece_based:
                    rec.pieces_per_crate = 0
                    rec.allow_half_crate = True
                else:
                    rec.pieces_per_crate = tmpl.milk_pieces_per_crate or 0
                    rec.allow_half_crate = tmpl.milk_allow_half_crate
            else:
                rec.is_piece_based = False
                rec.pieces_per_crate = 0
                rec.allow_half_crate = True

    @api.depends('qty', 'pieces_per_crate')
    def _compute_pieces_qty(self):
        for rec in self:
            # piece-based: pieces_per_crate is always 0, so this stays 0.
            # crate-based with pieces_per_crate > 0: qty × pieces_per_crate.
            if rec.pieces_per_crate and rec.pieces_per_crate > 0:
                rec.pieces_qty = rec.qty * rec.pieces_per_crate
            else:
                rec.pieces_qty = 0.0

    @api.depends('qty', 'rate')
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.qty * rec.rate

    # ── Onchange: rate resolution (milk_rate_per_crate > lst_price) ──────────

    @api.onchange('product_id')
    def _onchange_product(self):
        if not self.product_id:
            return

        # 1. Product-level milk_rate_per_crate (default rate, per crate or
        #    per piece depending on milk_is_piece_based — same field either way)
        tmpl = self.product_id.product_tmpl_id
        if tmpl.milk_rate_per_crate and tmpl.milk_rate_per_crate > 0:
            self.rate = tmpl.milk_rate_per_crate
            return

        # 2. Standard sales price fallback
        self.rate = self.product_id.lst_price

    # ── Constraints ───────────────────────────────────────────────────────────

    @api.constrains('qty', 'allow_half_crate', 'pieces_per_crate', 'is_piece_based')
    def _check_qty(self):
        for rec in self:
            if rec.qty < 0:
                raise ValidationError(
                    f"Negative quantity is not allowed "
                    f"(product: {rec.product_id.name})."
                )

            # Piece-based items: sold per piece — no crate step-rules apply.
            if rec.is_piece_based:
                continue

            # Only enforce crate step-rules when crate config is active.
            if not rec.pieces_per_crate:
                continue

            if not rec.allow_half_crate:
                # Must be a whole number (no fractional crates)
                if rec.qty != int(rec.qty):
                    raise ValidationError(
                        f"'{rec.product_id.name}' does not allow half-crate "
                        f"quantities. Please enter a whole number of crates "
                        f"(e.g. 1, 2, 3)."
                    )
            else:
                # Half-crate allowed: quantity must be a multiple of 0.5.
                # Multiply by 2 and verify it is a whole number.
                doubled = rec.qty * 2
                if abs(doubled - round(doubled)) > 1e-9:
                    raise ValidationError(
                        f"'{rec.product_id.name}' accepts quantities in "
                        f"half-crate steps only (e.g. 0.5, 1.0, 1.5, 2.0). "
                        f"Entered: {rec.qty}"
                    )

    # ── SQL constraint ────────────────────────────────────────────────────────

    @api.constrains('dispatch_line_id', 'product_id')
    def _check_unique_product_per_line(self):
        for rec in self:
            if self.search_count([
                ('dispatch_line_id', '=', rec.dispatch_line_id.id),
                ('product_id', '=', rec.product_id.id),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("The same product cannot appear twice for a dealer.")
