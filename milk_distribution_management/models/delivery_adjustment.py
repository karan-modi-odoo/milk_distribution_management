import logging
from odoo import models, fields, api, exceptions

_logger = logging.getLogger(__name__)


class MilkDeliveryAdjustment(models.Model):
    """
    Post-confirmation delivery adjustment for a confirmed dispatch sheet.

    Workflow:
        draft → submitted → approved  (triggers accounting move + ledger correction)
                          → rejected

    adjustment_type controls direction:
        deduction — driver delivered less than planned:
            • Reduces product qty on the dispatch line
            • Creates an out_refund (credit note)
            • Reduces today_bill in the partner ledger

        addition — driver delivered more than planned:
            • Increases (or creates) product qty on the dispatch line
            • Creates an out_invoice (debit invoice)
            • Increases today_bill in the partner ledger

    Only group_milk_manager users can approve or reject.
    """
    _name = 'milk.delivery.adjustment'
    _description = 'Delivery Adjustment'
    _order = 'date desc, name desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # ── Sequence ─────────────────────────────────────────────────────────────
    name = fields.Char(
        string='Reference', copy=False, readonly=True, default='New',
    )

    # ── Type ─────────────────────────────────────────────────────────────────
    adjustment_type = fields.Selection([
        ('deduction', 'Deduction'),
        ('addition', 'Addition'),
    ], string='Type', required=True, default='deduction', tracking=True,
        help='Deduction: driver delivered less than planned (credit note).\n'
             'Addition: driver delivered more than planned (debit invoice).',
    )

    # ── Core links ────────────────────────────────────────────────────────────
    sheet_id = fields.Many2one(
        'milk.dispatch.sheet', string='Dispatch Sheet',
        required=True, ondelete='restrict',
        domain=[('state', 'in', ('confirmed', 'delivered'))],
        tracking=True,
    )
    dispatch_line_id = fields.Many2one(
        'milk.dispatch.line', string='Dealer Line',
        required=True, ondelete='restrict',
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Dealer',
        related='dispatch_line_id.partner_id', store=True, readonly=True,
    )
    date = fields.Date(
        string='Adjustment Date', required=True,
        default=fields.Date.today, tracking=True,
    )

    # ── Adjustment lines ──────────────────────────────────────────────────────
    line_ids = fields.One2many(
        'milk.delivery.adjustment.line', 'adjustment_id',
        string='Products',
    )

    # ── Totals ────────────────────────────────────────────────────────────────
    total_adjustment = fields.Float(
        compute='_compute_total', string='Total Amount (Rs)',
        digits=(16, 2), store=True,
    )

    # ── Reason / notes ────────────────────────────────────────────────────────
    reason = fields.Text(string='Reason', required=True, tracking=True)

    # ── State ─────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='draft', required=True, tracking=True, string='Status')

    # ── Accounting link ───────────────────────────────────────────────────────
    # Field name kept as credit_note_id for DB column compatibility.
    # The string label reflects that it can be either a credit note or an invoice.
    credit_note_id = fields.Many2one(
        'account.move', string='Accounting Entry',
        readonly=True, copy=False,
    )

    # ── Compute ───────────────────────────────────────────────────────────────

    @api.depends('line_ids.adjustment_amount')
    def _compute_total(self):
        for rec in self:
            rec.total_adjustment = sum(rec.line_ids.mapped('adjustment_amount'))

    # ── Onchange helpers ──────────────────────────────────────────────────────

    @api.onchange('sheet_id')
    def _onchange_sheet_id(self):
        self.dispatch_line_id = False
        self.line_ids = [(5, 0, 0)]

    @api.onchange('sheet_id')
    def _onchange_sheet_domain(self):
        if self.sheet_id:
            return {'domain': {
                'dispatch_line_id': [('sheet_id', '=', self.sheet_id.id)],
            }}
        return {'domain': {'dispatch_line_id': []}}

    @api.onchange('adjustment_type')
    def _onchange_adjustment_type(self):
        """Clear lines when the type changes — different entry requirements."""
        self.line_ids = [(5, 0, 0)]

    @api.onchange('dispatch_line_id')
    def _onchange_dispatch_line_id(self):
        self.line_ids = [(5, 0, 0)]
        if not self.dispatch_line_id:
            return
        if self.adjustment_type == 'addition':
            # Addition: user enters products manually — no pre-population.
            return
        # Deduction: pre-populate from the dispatch line's product lines.
        lines = []
        for pl in self.dispatch_line_id.product_line_ids:
            if pl.qty > 0:
                lines.append((0, 0, {
                    'product_line_id': pl.id,
                    'product_id': pl.product_id.id,
                    'original_qty': pl.qty,
                    'original_rate': pl.rate,
                    'adjust_qty': 0.0,
                }))
        self.line_ids = lines

    # ── Workflow actions ──────────────────────────────────────────────────────

    def action_submit(self):
        for rec in self:
            rec._validate_lines()
            if rec.state != 'draft':
                raise exceptions.UserError(
                    "Only draft adjustments can be submitted."
                )
            rec.state = 'submitted'

    def action_approve(self):
        """Manager-only: apply corrections and create accounting entry."""
        self._check_manager()
        for rec in self:
            if rec.state != 'submitted':
                raise exceptions.UserError(
                    "Only submitted adjustments can be approved."
                )
            rec._validate_lines()
            rec._apply_dispatch_correction()
            rec._correct_ledger()
            accounting_move = rec._create_accounting_move()
            # Assign sequence BEFORE setting state to 'approved' so the
            # write guard (which blocks writes on approved records) does not
            # prevent the name from being saved.
            seq_name = rec.name
            if not seq_name or seq_name == 'New':
                seq_name = (
                        self.env['ir.sequence'].next_by_code(
                            'milk.delivery.adjustment'
                        ) or 'ADJ/NEW'
                )
            rec.write({
                'state': 'approved',
                'name': seq_name,
                'credit_note_id': accounting_move.id if accounting_move else False,
            })

    def action_reject(self):
        """Manager-only: reject without any changes."""
        self._check_manager()
        for rec in self:
            if rec.state not in ('draft', 'submitted'):
                raise exceptions.UserError(
                    "Only draft or submitted adjustments can be rejected."
                )
            rec.state = 'rejected'

    def action_reset_draft(self):
        for rec in self:
            if rec.state == 'rejected':
                rec.state = 'draft'

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_manager(self):
        if not self.env.user.has_group(
                'milk_distribution_management.group_milk_manager'
        ):
            raise exceptions.AccessError(
                "Only Milk ERP Managers can approve or reject adjustments."
            )

    def _validate_lines(self):
        for rec in self:
            if not rec.line_ids:
                raise exceptions.UserError(
                    "Add at least one product line before proceeding."
                )
            # For deductions, lines are pre-filled for ALL products on the
            # dispatch line. The user only fills the products they want to
            # adjust — zero-qty lines are valid placeholders and are skipped.
            active_lines = [l for l in rec.line_ids if l.adjust_qty > 0]
            if not active_lines:
                raise exceptions.UserError(
                    "Enter a quantity greater than zero for at least one product."
                )
            for line in active_lines:
                if rec.adjustment_type == 'deduction':
                    if line.adjust_qty > line.original_qty:
                        raise exceptions.UserError(
                            f"Deduction qty ({line.adjust_qty}) for "
                            f"'{line.product_id.name}' exceeds the original "
                            f"delivered qty ({line.original_qty})."
                        )

    def _apply_dispatch_correction(self):
        """
        Update product qty on the original dispatch line.

        Deduction: reduce qty by adjust_qty (uses the stored product_line_id).
        Addition : increase qty if the product already exists on the dispatch
                   line, otherwise create a new product line for it.
        Uses sudo() to bypass the confirmed-sheet write-guard.
        """
        for rec in self:
            for adj_line in rec.line_ids:
                if adj_line.adjust_qty <= 0:
                    continue

                if rec.adjustment_type == 'deduction':
                    pl = adj_line.product_line_id.sudo()
                    new_qty = adj_line.original_qty - adj_line.adjust_qty
                    pl.write({'qty': new_qty})

                else:  # addition
                    existing_pl = rec.dispatch_line_id.sudo().product_line_ids.filtered(
                        lambda p: p.product_id == adj_line.product_id
                    )
                    if existing_pl:
                        existing_pl[0].write({
                            'qty': existing_pl[0].qty + adj_line.adjust_qty,
                        })
                    else:
                        self.env['milk.dispatch.product.line'].sudo().create({
                            'dispatch_line_id': rec.dispatch_line_id.id,
                            'product_id': adj_line.product_id.id,
                            'qty': adj_line.adjust_qty,
                            'rate': adj_line.original_rate,
                        })

            # Trigger stored compute on the dispatch line total
            rec.dispatch_line_id.sudo()._compute_total()

    def _correct_ledger(self):
        """
        Adjust today_bill in milk.partner.ledger for the dispatch sheet date.

        Deduction: subtract adjustment amount (with floor at 0).
        Addition : add adjustment amount.
        """
        for rec in self:
            if rec.total_adjustment <= 0:
                continue
            ledger = self.env['milk.partner.ledger'].search([
                ('partner_id', '=', rec.partner_id.id),
                ('date', '=', rec.sheet_id.date),
            ], limit=1)
            if not ledger:
                _logger.warning(
                    "MilkDeliveryAdjustment: no ledger row found for partner "
                    "%s on %s — ledger correction skipped.",
                    rec.partner_id.name, rec.sheet_id.date,
                )
                continue
            if rec.adjustment_type == 'deduction':
                ledger.write({
                    'today_bill': max(0.0, ledger.today_bill - rec.total_adjustment),
                })
            else:  # addition
                ledger.write({
                    'today_bill': ledger.today_bill + rec.total_adjustment,
                })

    def _create_accounting_move(self):
        """
        Create an accounting move for the adjustment.

        Deduction → out_refund (credit note).
        Addition  → out_invoice (debit invoice).
        Returns the created move or False if accounting is not configured.
        """
        self.ensure_one()
        partner = self.partner_id

        if self.adjustment_type == 'deduction':
            move_type = 'out_refund'
            ref_prefix = 'ADJ-DEDUCT'
        else:
            move_type = 'out_invoice'
            ref_prefix = 'ADJ-ADD'

        invoice_lines = []
        for adj_line in self.line_ids:
            if adj_line.adjust_qty <= 0:
                continue
            invoice_lines.append((0, 0, {
                'name': (
                    f"Delivery Adjustment — {adj_line.product_id.name} "
                    f"({self.sheet_id.name})"
                ),
                'quantity': adj_line.adjust_qty,
                'price_unit': adj_line.original_rate,
                'product_id': adj_line.product_id.id,
            }))

        if not invoice_lines:
            return False

        move_vals = {
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': self.date,
            'invoice_date_due': self.date,
            'ref': f"{ref_prefix}: {self.sheet_id.name} / {self.reason[:60]}",
            'invoice_line_ids': invoice_lines,
            'narration': self.reason,
        }
        move = self.env['account.move'].sudo().create(move_vals)
        move.action_post()
        return move

    # ── ORM overrides ─────────────────────────────────────────────────────────

    def write(self, vals):
        for rec in self:
            if rec.state == 'approved' and not self.env.su:
                raise exceptions.UserError(
                    "Approved adjustments cannot be modified."
                )
        return super().write(vals)


class MilkDeliveryAdjustmentLine(models.Model):
    """
    One product line per adjustment.

    For deductions: product_line_id links to the original dispatch product line;
                    original_qty and original_rate are auto-populated.
    For additions : product_line_id is empty; user picks product and rate freely.
    """
    _name = 'milk.delivery.adjustment.line'
    _description = 'Delivery Adjustment Line'

    adjustment_id = fields.Many2one(
        'milk.delivery.adjustment', ondelete='cascade',
    )
    product_line_id = fields.Many2one(
        'milk.dispatch.product.line', string='Original Product Line',
        ondelete='restrict',
        help='Linked to the dispatch product line for deductions. '
             'Empty for additions.',
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
    )
    original_qty = fields.Float(
        string='Original Qty', digits=(16, 3),
        help='Pre-filled for deductions. Zero for additions (no original).',
    )
    original_rate = fields.Float(
        string='Rate (Rs)', digits=(16, 2),
        help='For deductions: rate from the dispatch line. '
             'For additions: enter the billing rate.',
    )
    adjust_qty = fields.Float(
        string='Qty', digits=(16, 3),
        help='Quantity to deduct (deduction) or add (addition).',
    )
    adjustment_amount = fields.Float(
        compute='_compute_amount', string='Amount (Rs)',
        digits=(16, 2), store=True,
    )

    @api.depends('adjust_qty', 'original_rate')
    def _compute_amount(self):
        for rec in self:
            rec.adjustment_amount = rec.adjust_qty * rec.original_rate
