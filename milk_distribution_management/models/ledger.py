import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class MilkPartnerLedger(models.Model):
    _name = 'milk.partner.ledger'
    _description = 'Milk Partner Ledger'
    _order = 'date desc, partner_id'

    partner_id = fields.Many2one('res.partner', string='Dealer', required=True, index=True)
    date = fields.Date(string='Date', required=True, index=True)
    opening_balance = fields.Float(string='Opening (Rs)', digits=(16, 2))
    today_bill = fields.Float(string="Today's Bill (Rs)", digits=(16, 2))
    received_amount = fields.Float(string='Cash Received (Rs)', digits=(16, 2))
    closing_balance = fields.Float(
        compute='_compute_closing',
        string='Closing (Rs)',
        digits=(16, 2),
    )

    @api.depends('opening_balance', 'today_bill', 'received_amount')
    def _compute_closing(self):
        for rec in self:
            rec.closing_balance = rec.opening_balance + rec.today_bill - rec.received_amount

    # ── Feature 4: Auto Carry Forward ────────────────────────────────────────
    def action_auto_carry_forward(self):
        """
        Called by daily cron.
        For every dealer that has a previous ledger entry but NO entry today,
        create today's entry using yesterday's closing as opening balance.
        This ensures the ledger is always continuous even on no-dispatch days.
        """
        today = fields.Date.today()
        all_partner_ids = self.search([('date', '<', today)]).mapped('partner_id').ids

        for partner_id in set(all_partner_ids):
            existing_today = self.search([
                ('partner_id', '=', partner_id),
                ('date', '=', today),
            ], limit=1)
            if existing_today:
                continue

            last = self.search([
                ('partner_id', '=', partner_id),
                ('date', '<', today),
            ], order='date desc', limit=1)

            if last and last.closing_balance != 0:
                self.sudo().create({
                    'partner_id': partner_id,
                    'date': today,
                    'opening_balance': last.closing_balance,
                    'today_bill': 0.0,
                    'received_amount': 0.0,
                })

    # ── Feature 5: Weekly WhatsApp Summary ───────────────────────────────────
    def action_send_weekly_whatsapp(self):
        """
        Called by weekly cron (every Sunday).
        Sends each dealer with a positive outstanding their current balance via WhatsApp.
        """
        if 'whatsapp.message' not in self.env:
            _logger.info("Weekly WhatsApp: whatsapp module not installed.")
            return
        try:
            wa_account = self.env['whatsapp.account'].search([('active', '=', True)], limit=1)
            if not wa_account:
                _logger.info("Weekly WhatsApp: No active WhatsApp account configured.")
                return

            import datetime
            date_str = datetime.date.today().strftime('%d-%m-%Y')
            company = self.env.company.name

            all_partners = self.search([]).mapped('partner_id')
            for partner in all_partners:
                mobile = partner.mobile or partner.phone
                if not mobile:
                    continue

                last = self.search([
                    ('partner_id', '=', partner.id),
                ], order='date desc', limit=1)

                if not last or last.closing_balance <= 0:
                    continue

                message = (
                    f"*{company}*\n"
                    f"Weekly Outstanding Summary — {date_str}\n\n"
                    f"Dear {partner.name},\n"
                    f"Your current outstanding balance is:\n"
                    f"*Rs {last.closing_balance:,.2f}*\n\n"
                    f"Please arrange payment at your earliest.\n"
                    f"Thank you!"
                )
                try:
                    self.env['whatsapp.message'].sudo().create({
                        'mobile_number': mobile,
                        'body': message,
                        'partner_id': partner.id,
                        'account_id': wa_account.id,
                    }).send()
                except Exception as exc:
                    _logger.warning("Weekly WhatsApp failed for %s: %s", partner.name, exc)

        except Exception as exc:
            _logger.warning("Weekly WhatsApp cron error: %s", exc)
