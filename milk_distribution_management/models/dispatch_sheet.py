import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ROUTE_SELECTION = [
    ('rashmi_am', 'Rashmi AM'),
    ('giriraj_am', 'Giriraj AM'),
    ('pm', 'PM'),
]


class MilkDispatchSheet(models.Model):
    _name = 'milk.dispatch.sheet'
    _description = 'Milk Dispatch Sheet'
    _order = 'date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(string='Reference', default='New', readonly=True, copy=False, tracking=True)
    date = fields.Date(required=True, default=fields.Date.today, tracking=True)
    driver_id = fields.Many2one('res.partner', string='Driver', tracking=True)
    route = fields.Selection(ROUTE_SELECTION, required=True, string='Route', tracking=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed')],
        default='draft', string='Status', tracking=True,
    )
    invoice_created = fields.Boolean(default=False, copy=False)
    line_ids = fields.One2many('milk.dispatch.line', 'sheet_id', string='Dealer Lines')
    invoice_ids = fields.Many2many('account.move', string='Invoices', readonly=True, copy=False)

    total_dealers = fields.Integer(compute='_compute_totals', string='Dealers')
    total_amount = fields.Float(compute='_compute_totals', string='Total Amount', digits=(16, 2))
    invoice_count = fields.Integer(compute='_compute_invoice_count', string='Invoices')

    @api.depends('line_ids', 'line_ids.total_amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_dealers = len(rec.line_ids)
            rec.total_amount = sum(rec.line_ids.mapped('total_amount'))

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('milk.dispatch.sheet') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        protected = set(vals.keys()) - {
            'state', 'invoice_created', 'invoice_ids',
            'message_follower_ids', 'activity_ids',
        }
        if protected:
            for rec in self:
                if rec.state == 'confirmed':
                    raise UserError(f"'{rec.name}' is confirmed and cannot be edited.")
        return super().write(vals)

    def action_confirm(self):
        for rec in self:
            if rec.invoice_created:
                raise UserError(f"'{rec.name}' has already been processed.")
            if not rec.line_ids:
                raise UserError("Add at least one dealer line before confirming.")

            created_invoices = self.env['account.move']

            for line in rec.line_ids:
                # ── Credit Limit Check (Feature 7) ────────────────────────
                if line.partner_id.milk_credit_limit > 0:
                    last_ledger = self.env['milk.partner.ledger'].search([
                        ('partner_id', '=', line.partner_id.id),
                    ], order='date desc', limit=1)
                    current_outstanding = last_ledger.closing_balance if last_ledger else 0.0
                    new_bill = sum(
                        pl.qty * pl.rate for pl in line.product_line_ids if pl.qty > 0
                    )
                    if current_outstanding + new_bill > line.partner_id.milk_credit_limit:
                        raise UserError(
                            f"Dealer '{line.partner_id.name}' has exceeded the credit limit!\n\n"
                            f"Current Outstanding : Rs {current_outstanding:,.2f}\n"
                            f"New Bill            : Rs {new_bill:,.2f}\n"
                            f"Credit Limit        : Rs {line.partner_id.milk_credit_limit:,.2f}\n\n"
                            f"Please collect payment first or increase the credit limit on the dealer's contact."
                        )

                # ── Build invoice lines ───────────────────────────────────
                invoice_lines = [
                    (0, 0, {
                        'product_id': pl.product_id.id,
                        'quantity': pl.qty,
                        'price_unit': pl.rate,
                    })
                    for pl in line.product_line_ids if pl.qty > 0
                ]
                if not invoice_lines:
                    continue

                # ── Create and post invoice ───────────────────────────────
                invoice = self.env['account.move'].create({
                    'move_type': 'out_invoice',
                    'partner_id': line.partner_id.id,
                    'invoice_date': rec.date,
                    'invoice_line_ids': invoice_lines,
                })
                invoice.action_post()
                created_invoices |= invoice

                # ── Same-day ledger logic ─────────────────────────────────
                existing = self.env['milk.partner.ledger'].search([
                    ('partner_id', '=', line.partner_id.id),
                    ('date', '=', rec.date),
                ], limit=1)

                if existing:
                    existing.sudo().write({
                        'today_bill': existing.today_bill + invoice.amount_total,
                    })
                else:
                    last = self.env['milk.partner.ledger'].search([
                        ('partner_id', '=', line.partner_id.id),
                        ('date', '<', rec.date),
                    ], order='date desc', limit=1)
                    opening = last.closing_balance if last else 0.0
                    self.env['milk.partner.ledger'].sudo().create({
                        'partner_id': line.partner_id.id,
                        'date': rec.date,
                        'opening_balance': opening,
                        'today_bill': invoice.amount_total,
                        'received_amount': 0.0,
                    })

                rec._send_whatsapp_bill(line.partner_id, invoice.amount_total)

            rec.sudo().write({
                'invoice_created': True,
                'state': 'confirmed',
                'invoice_ids': [(4, inv.id) for inv in created_invoices],
            })

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
        }

    def _send_whatsapp_bill(self, partner, bill_amount):
        if 'whatsapp.message' not in self.env:
            return
        try:
            wa_account = self.env['whatsapp.account'].search([('active', '=', True)], limit=1)
            if not wa_account:
                return
            mobile = partner.mobile or partner.phone
            if not mobile:
                return
            ledger = self.env['milk.partner.ledger'].search([
                ('partner_id', '=', partner.id),
                ('date', '=', self.date),
            ], limit=1)
            route_label = dict(ROUTE_SELECTION).get(self.route, '')
            opening = ledger.opening_balance if ledger else 0.0
            total_due = ledger.closing_balance if ledger else bill_amount
            date_str = self.date.strftime('%d-%m-%Y')
            company = self.env.company.name
            message = (
                f"*{company}*\n"
                f"Dear {partner.name},\n\n"
                f"Date: {date_str} | Route: {route_label}\n"
                f"Opening Balance : Rs {opening:,.2f}\n"
                f"Today's Bill    : Rs {bill_amount:,.2f}\n"
                f"*Total Due      : Rs {total_due:,.2f}*\n\n"
                f"Please arrange payment. Thank you!"
            )
            self.env['whatsapp.message'].sudo().create({
                'mobile_number': mobile,
                'body': message,
                'partner_id': partner.id,
                'account_id': wa_account.id,
            }).send()
        except Exception as exc:
            _logger.warning("WhatsApp failed for %s: %s", partner.name, exc)
