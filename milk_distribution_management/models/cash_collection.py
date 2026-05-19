from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class MilkCashCollection(models.Model):
    _name = 'milk.cash.collection'
    _description = 'Milk Cash Collection'
    _order = 'date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    date = fields.Date(default=fields.Date.today, string='Date', required=True, tracking=True)
    collector_id = fields.Many2one(
        'res.partner',
        string='Collector',
        tracking=True,
        domain=[('milk_is_collector', '=', True)],
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed')],
        default='draft', string='Status', tracking=True,
    )
    line_ids = fields.One2many('milk.cash.collection.line', 'collection_id', string='Lines')
    total_collected = fields.Float(compute='_compute_total', string='Total Collected (Rs)', digits=(16, 2))

    @api.depends('line_ids.collected_amount')
    def _compute_total(self):
        for rec in self:
            rec.total_collected = sum(rec.line_ids.mapped('collected_amount'))

    def action_confirm(self):
        for rec in self:
            if rec.state == 'confirmed':
                raise UserError("Already confirmed. Cannot confirm again.")
            if not rec.line_ids:
                raise UserError("Add at least one dealer line before confirming.")

            for line in rec.line_ids:
                if not line.collected_amount:
                    continue
                # Search for the ledger row that matches the collection date
                # exactly so that late-confirmed collections credit the correct
                # day's row instead of always hitting the most recent one.
                ledger = self.env['milk.partner.ledger'].search([
                    ('partner_id', '=', line.partner_id.id),
                    ('date', '=', rec.date),
                ], limit=1)
                if not ledger:
                    # Fallback: no row exists for this date yet — find the
                    # nearest previous row so the payment is not lost.
                    ledger = self.env['milk.partner.ledger'].search([
                        ('partner_id', '=', line.partner_id.id),
                        ('date', '<=', rec.date),
                    ], order='date desc', limit=1)
                if not ledger:
                    raise UserError(
                        f"No ledger entry found for '{line.partner_id.name}'. "
                        "Confirm a dispatch sheet for this dealer first."
                    )
                ledger.sudo().write({
                    'received_amount': ledger.received_amount + line.collected_amount,
                })
            rec.state = 'confirmed'

    # Feature 6: Print Payment Receipt
    def action_print_receipt(self):
        return self.env.ref(
            'milk_distribution_management.action_report_payment_receipt'
        ).report_action(self)


class MilkCashCollectionLine(models.Model):
    _name = 'milk.cash.collection.line'
    _description = 'Milk Cash Collection Line'

    collection_id = fields.Many2one('milk.cash.collection', ondelete='cascade')
    partner_id = fields.Many2one(
        'res.partner',
        string='Dealer',
        required=True,
        domain=[('milk_is_dealer', '=', True)],
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
    outstanding = fields.Float(compute='_compute_outstanding', string='Outstanding (Rs)', digits=(16, 2))
    collected_amount = fields.Float(string='Collected (Rs)', digits=(16, 2))
    balance_after = fields.Float(compute='_compute_balance_after', string='Balance After (Rs)', digits=(16, 2))

    @api.depends('partner_id')
    def _compute_outstanding(self):
        for rec in self:
            if not rec.partner_id:
                rec.outstanding = 0.0
                continue
            ledger = self.env['milk.partner.ledger'].search([
                ('partner_id', '=', rec.partner_id.id),
            ], order='date desc', limit=1)
            rec.outstanding = ledger.closing_balance if ledger else 0.0

    @api.depends('outstanding', 'collected_amount')
    def _compute_balance_after(self):
        for rec in self:
            rec.balance_after = rec.outstanding - rec.collected_amount

    @api.constrains('collected_amount')
    def _check_collected_amount(self):
        for rec in self:
            if rec.collected_amount < 0:
                raise ValidationError(
                    f"Collected amount cannot be negative for dealer '{rec.partner_id.name}'."
                )
