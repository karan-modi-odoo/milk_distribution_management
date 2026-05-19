from odoo import models, fields, api
from odoo.exceptions import UserError


class MilkQuickCashWizard(models.TransientModel):
    """
    Bulk Cash Collection Wizard.

    Allows fast dealer-wise cash collection entry in a single dialog.
    Each line shows a dealer's current outstanding balance and accepts
    an inline amount + payment mode input.

    On save (action_collect), a single milk.cash.collection record is
    created with one milk.cash.collection.line per dealer that has a
    non-zero collected_amount.  The existing action_confirm() logic on
    milk.cash.collection is then called unchanged — no business logic is
    duplicated here.
    """

    _name = 'milk.quick.cash.wizard'
    _description = 'Bulk Cash Collection Wizard'

    date = fields.Date(
        string='Collection Date',
        required=True,
        default=fields.Date.today,
    )
    collector_id = fields.Many2one(
        'res.partner',
        string='Collector',
        domain=[('milk_is_collector', '=', True)],
    )
    line_ids = fields.One2many(
        'milk.quick.cash.wizard.line',
        'wizard_id',
        string='Dealer Lines',
    )
    total_amount = fields.Float(
        compute='_compute_total_amount',
        string='Total to Collect (Rs)',
        digits=(16, 2),
    )

    @api.depends('line_ids.collected_amount')
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped('collected_amount'))

    # ── Default population ────────────────────────────────────────────────────

    @api.model
    def default_get(self, fields_list):
        """
        Pre-populate wizard lines with every dealer that has a positive
        outstanding balance (latest closing_balance > 0 in milk.partner.ledger).

        Lines are ordered by dealer name for easy scanning.
        """
        res = super().default_get(fields_list)

        if 'line_ids' not in fields_list:
            return res

        Ledger = self.env['milk.partner.ledger']

        # Fetch the most recent ledger entry per partner
        # using a subquery-style approach: group by partner, take max date
        all_latest = Ledger.search([], order='partner_id asc, date desc')

        seen_partners = set()
        line_vals = []

        for entry in all_latest:
            pid = entry.partner_id.id
            if pid in seen_partners:
                continue
            seen_partners.add(pid)

            # Only include dealers with a positive outstanding balance
            if entry.closing_balance <= 0:
                continue

            line_vals.append((0, 0, {
                'partner_id': pid,
                'outstanding': entry.closing_balance,
                'collected_amount': 0.0,
                'payment_mode': 'cash',
            }))

        # Sort lines alphabetically by dealer name for readability
        line_vals.sort(key=lambda v: self.env['res.partner'].browse(v[2]['partner_id']).name or '')

        if line_vals:
            res['line_ids'] = line_vals

        return res

    # ── Save action ───────────────────────────────────────────────────────────

    def action_collect(self):
        """
        Create a single milk.cash.collection with all lines where
        collected_amount > 0, then call the existing action_confirm()
        without modification.

        Returns an act_window action that opens the newly created record.
        """
        self.ensure_one()

        active_lines = self.line_ids.filtered(
            lambda l: l.collected_amount > 0
        )

        if not active_lines:
            raise UserError(
                "Please enter a collected amount for at least one dealer "
                "before saving."
            )

        # Build collection line values using the same fields that
        # milk.cash.collection.line expects — no extra fields added.
        collection_line_vals = [
            (0, 0, {
                'partner_id': line.partner_id.id,
                'payment_mode': line.payment_mode,
                'collected_amount': line.collected_amount,
            })
            for line in active_lines
        ]

        collection = self.env['milk.cash.collection'].create({
            'date': self.date,
            'collector_id': self.collector_id.id or False,
            'line_ids': collection_line_vals,
        })

        # Delegate confirmation to the existing, unmodified action_confirm()
        collection.action_confirm()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Cash Collection',
            'res_model': 'milk.cash.collection',
            'res_id': collection.id,
            'view_mode': 'form',
            'target': 'current',
        }


class MilkQuickCashWizardLine(models.TransientModel):
    """
    One line per dealer in the Bulk Cash Collection Wizard.

    outstanding  — read from the latest milk.partner.ledger row (pre-filled,
                   display-only).
    balance_after — live computed preview so the collector sees the result
                    before saving.
    """

    _name = 'milk.quick.cash.wizard.line'
    _description = 'Bulk Cash Collection Wizard Line'
    _order = 'partner_id'

    wizard_id = fields.Many2one(
        'milk.quick.cash.wizard',
        ondelete='cascade',
        required=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Dealer',
        required=True,
        domain=[('milk_is_dealer', '=', True)],
    )
    outstanding = fields.Float(
        string='Outstanding (Rs)',
        digits=(16, 2),
        readonly=True,
    )
    payment_mode = fields.Selection(
        [
            ('cash', 'Cash'),
            ('upi', 'UPI'),
            ('cheque', 'Cheque'),
            ('neft', 'NEFT / RTGS'),
        ],
        string='Payment Mode',
        required=True,
        default='cash',
    )
    collected_amount = fields.Float(
        string='Collected (Rs)',
        digits=(16, 2),
        default=0.0,
    )
    balance_after = fields.Float(
        compute='_compute_balance_after',
        string='Balance After (Rs)',
        digits=(16, 2),
    )

    @api.depends('outstanding', 'collected_amount')
    def _compute_balance_after(self):
        for rec in self:
            rec.balance_after = rec.outstanding - rec.collected_amount
