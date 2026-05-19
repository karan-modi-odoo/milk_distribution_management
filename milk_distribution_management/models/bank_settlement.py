import logging
from odoo import models, fields, api, exceptions

_logger = logging.getLogger(__name__)


class MilkBankDeposit(models.Model):
    """
    Records cash deposited by the distributor into his bank account.

    Standalone tracking only — no account.payment created./home/admionix-1/Downloads/icon.png
    Workflow: draft → confirmed → cancelled
    """
    _name = 'milk.bank.deposit'
    _description = 'Bank Deposit'
    _order = 'date desc, name desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Reference', copy=False, readonly=True, default='New',
    )
    date = fields.Date(
        string='Deposit Date', required=True,
        default=fields.Date.today, tracking=True,
    )
    bank_name = fields.Char(
        string='Bank Name', required=True, tracking=True,
    )
    account_number = fields.Char(
        string='Account Number / IFSC',
    )
    amount = fields.Float(
        string='Deposit Amount (Rs)', required=True,
        digits=(16, 2), tracking=True,
    )
    deposit_slip_ref = fields.Char(
        string='Slip / UTR Reference',
        help="Bank deposit slip number or UTR for online transfer.",
    )
    # Link to one or more cash collection records this deposit covers
    collection_ids = fields.Many2many(
        'milk.cash.collection',
        'milk_bank_deposit_collection_rel',
        'deposit_id', 'collection_id',
        string='Cash Collections Covered',
        domain=[('state', '=', 'confirmed')],
    )
    notes = fields.Text(string='Notes')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, string='Status')

    # ── Computed: total of linked collections ─────────────────────────────
    total_collections = fields.Float(
        compute='_compute_total_collections',
        string='Total Collections (Rs)', digits=(16, 2),
    )
    difference = fields.Float(
        compute='_compute_total_collections',
        string='Difference (Rs)', digits=(16, 2),
        help="Deposit amount minus total linked collections.",
    )

    @api.depends('collection_ids.total_collected', 'amount')
    def _compute_total_collections(self):
        for rec in self:
            total = sum(rec.collection_ids.mapped('total_collected'))
            rec.total_collections = total
            rec.difference = rec.amount - total

    # ── Workflow ─────────────────────────────────────────────────────────
    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise exceptions.UserError(
                    "Only draft deposits can be confirmed."
                )
            if rec.amount <= 0:
                raise exceptions.UserError(
                    "Deposit amount must be greater than zero."
                )
            if rec.name == 'New':
                rec.name = self.env['ir.sequence'].next_by_code(
                    'milk.bank.deposit'
                ) or 'BDP/NEW'
            rec.state = 'confirmed'

    def action_cancel(self):
        for rec in self:
            if rec.state == 'confirmed':
                raise exceptions.UserError(
                    "Confirmed deposits cannot be cancelled. "
                    "Contact your manager."
                )
            rec.state = 'cancelled'

    def action_reset_draft(self):
        for rec in self:
            if rec.state == 'cancelled':
                rec.state = 'draft'

    def write(self, vals):
        for rec in self:
            if rec.state == 'confirmed' and not self.env.su:
                raise exceptions.UserError(
                    "Confirmed bank deposits cannot be modified."
                )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                        self.env['ir.sequence'].next_by_code('milk.bank.deposit')
                        or 'New'
                )
        return super().create(vals_list)


class MilkDairyTransfer(models.Model):
    """
    Records a payment made by the distributor to the dairy
    against a milk.dairy.purchase bill.

    Standalone tracking only — no account.payment created.
    Workflow: draft → confirmed → cancelled
    """
    _name = 'milk.dairy.transfer'
    _description = 'Dairy Transfer / Payment'
    _order = 'date desc, name desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Reference', copy=False, readonly=True, default='New',
    )
    date = fields.Date(
        string='Transfer Date', required=True,
        default=fields.Date.today, tracking=True,
    )
    dairy_name = fields.Char(
        string='Dairy Name', required=True, tracking=True,
    )
    payment_mode = fields.Selection([
        ('neft', 'NEFT'),
        ('rtgs', 'RTGS'),
        ('cheque', 'Cheque'),
        ('upi', 'UPI'),
        ('cash', 'Cash'),
    ], string='Payment Mode', required=True, default='neft', tracking=True)
    utr_ref = fields.Char(
        string='UTR / Cheque No.',
        help="UTR number for NEFT/RTGS, cheque number for cheque payments.",
    )
    amount = fields.Float(
        string='Transfer Amount (Rs)', required=True,
        digits=(16, 2), tracking=True,
    )
    # Link to the dairy purchase bill this payment settles
    purchase_id = fields.Many2one(
        'milk.dairy.purchase', string='Dairy Purchase Bill',
        ondelete='restrict', tracking=True,
    )
    notes = fields.Text(string='Notes')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True, string='Status')

    # ── Computed: bill amount vs transfer amount ───────────────────────────
    bill_amount = fields.Float(
        related='purchase_id.total_amount',
        string='Bill Amount (Rs)', digits=(16, 2), readonly=True,
    )
    difference = fields.Float(
        compute='_compute_difference',
        string='Difference (Rs)', digits=(16, 2),
        help="Transfer amount minus linked bill amount.",
    )

    @api.depends('amount', 'purchase_id.total_amount')
    def _compute_difference(self):
        for rec in self:
            rec.difference = rec.amount - (rec.purchase_id.total_amount or 0.0)

    # ── Workflow ─────────────────────────────────────────────────────────
    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise exceptions.UserError(
                    "Only draft transfers can be confirmed."
                )
            if rec.amount <= 0:
                raise exceptions.UserError(
                    "Transfer amount must be greater than zero."
                )
            if rec.name == 'New':
                rec.name = self.env['ir.sequence'].next_by_code(
                    'milk.dairy.transfer'
                ) or 'DTR/NEW'
            rec.state = 'confirmed'

    def action_cancel(self):
        for rec in self:
            if rec.state == 'confirmed':
                raise exceptions.UserError(
                    "Confirmed transfers cannot be cancelled. "
                    "Contact your manager."
                )
            rec.state = 'cancelled'

    def action_reset_draft(self):
        for rec in self:
            if rec.state == 'cancelled':
                rec.state = 'draft'

    def write(self, vals):
        for rec in self:
            if rec.state == 'confirmed' and not self.env.su:
                raise exceptions.UserError(
                    "Confirmed dairy transfers cannot be modified."
                )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                        self.env['ir.sequence'].next_by_code('milk.dairy.transfer')
                        or 'New'
                )
        return super().create(vals_list)
