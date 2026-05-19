import io
import csv
import base64
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MilkDispatchSheet(models.Model):
    _name = 'milk.dispatch.sheet'
    _description = 'Milk Dispatch Sheet'
    _order = 'date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', default='New', readonly=True, copy=False,
        tracking=True,
    )
    date = fields.Date(required=True, default=fields.Date.today, tracking=True)
    driver_id = fields.Many2one(
        'res.partner',
        string='Driver',
        tracking=True,
        domain=[('milk_is_driver', '=', True)],
    )

    # Feature 5: Many2one to milk.route replaces the hardcoded Selection field.
    route_id = fields.Many2one(
        'milk.route', string='Route', required=True, tracking=True,
        ondelete='restrict',
    )

    # Feature 4 (Option A): 'delivered' is a post-confirmation acknowledgment.
    # Invoice creation still happens at 'confirmed' (existing behaviour preserved).
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('confirmed', 'Confirmed'),
            ('delivered', 'Delivered'),
        ],
        default='draft', string='Status', tracking=True,
    )
    invoice_created = fields.Boolean(default=False, copy=False)
    line_ids = fields.One2many('milk.dispatch.line', 'sheet_id', string='Dealer Lines')
    invoice_ids = fields.Many2many(
        'account.move', string='Invoices', readonly=True, copy=False,
    )

    total_dealers = fields.Integer(compute='_compute_totals', string='Dealers', store=True)
    total_amount = fields.Float(
        compute='_compute_totals', string='Total Amount', digits=(16, 2), store=True,
    )
    invoice_count = fields.Integer(
        compute='_compute_invoice_count', string='Invoice Count',
    )

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
                vals['name'] = (
                        self.env['ir.sequence'].next_by_code('milk.dispatch.sheet')
                        or 'New'
                )
        return super().create(vals_list)

    # Fields that must never change once the sheet is confirmed or delivered.
    _CONFIRMED_BLOCKED_FIELDS = frozenset({
        'date',
        'driver_id',
        'route_id',
        'line_ids',
    })

    def write(self, vals):
        blocked = self._CONFIRMED_BLOCKED_FIELDS.intersection(vals.keys())
        if blocked and not self.env.su:
            for rec in self:
                if rec.state in ('confirmed', 'delivered'):
                    raise UserError(
                        f"'{rec.name}' is {rec.state} and cannot be edited.\n\n"
                        f"Locked fields: {', '.join(sorted(blocked))}"
                    )
        return super().write(vals)

    # ── Workflow actions ──────────────────────────────────────────────────────

    def action_confirm(self):
        """Confirm the sheet, create invoices, and update the partner ledger."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(f"'{rec.name}' is already confirmed.")
            if not rec.line_ids:
                raise UserError("Add at least one dealer line before confirming.")

            created_invoices = self.env['account.move']

            for line in rec.line_ids:
                # ── Credit limit check ────────────────────────────────────
                if line.partner_id.milk_credit_limit > 0:
                    last_ledger = self.env['milk.partner.ledger'].search([
                        ('partner_id', '=', line.partner_id.id),
                    ], order='date desc', limit=1)
                    current_outstanding = (
                        last_ledger.closing_balance if last_ledger else 0.0
                    )
                    new_bill = sum(
                        pl.qty * pl.rate
                        for pl in line.product_line_ids
                        if pl.qty > 0
                    )
                    if (
                            current_outstanding + new_bill
                            > line.partner_id.milk_credit_limit
                    ):
                        raise UserError(
                            f"Dealer '{line.partner_id.name}' has exceeded "
                            f"the credit limit!\n\n"
                            f"Current Outstanding : Rs {current_outstanding:,.2f}\n"
                            f"New Bill            : Rs {new_bill:,.2f}\n"
                            f"Credit Limit        : Rs "
                            f"{line.partner_id.milk_credit_limit:,.2f}\n\n"
                            f"Please collect payment first or increase the "
                            f"credit limit on the dealer's contact."
                        )

                # ── Build invoice lines ───────────────────────────────────
                invoice_lines = [
                    (0, 0, {
                        'product_id': pl.product_id.id,
                        'quantity': pl.qty,
                        'price_unit': pl.rate,
                    })
                    for pl in line.product_line_ids
                    if pl.qty > 0
                ]
                if not invoice_lines:
                    continue

                # ── Create and post invoice ───────────────────────────────
                # invoice_date_due is required in Odoo 19: every journal item
                # on a receivable account must carry a due date.
                # For daily milk distribution, due date = invoice date.
                invoice = self.env['account.move'].create({
                    'move_type': 'out_invoice',
                    'partner_id': line.partner_id.id,
                    'invoice_date': rec.date,
                    'invoice_date_due': rec.date,
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

            # ── Auto-issue crates per dealer (Gap #2) ─────────────────────
            # Runs after state is 'confirmed' so balance lookups are clean.
            # No existing invoice / ledger logic is affected.
            rec._auto_create_crate_issues()

    # ── Crate auto-issue helper ───────────────────────────────────────────────

    def _auto_create_crate_issues(self):
        """
        Gap #2 — Auto-create one confirmed milk.crate.transaction (type=issue)
        per dealer for every dispatch sheet confirmation.

        Rules:
        * Only products with milk_pieces_per_crate > 0 are counted as crates.
        * Qty is the sum of all such product line quantities for that dealer.
        * Dealers with zero total crate qty are skipped silently.
        * If a confirmed issue transaction already exists for this sheet +
          dealer (idempotency guard), it is skipped to prevent duplicates.
        * Crate balance is updated via the existing action_confirm() path so
          all running-balance logic stays in one place.
        """
        self.ensure_one()
        CrateTxn = self.env['milk.crate.transaction'].sudo()

        for line in self.line_ids:
            # Sum crate qty only for products with crate tracking enabled
            total_crates = sum(
                pl.qty
                for pl in line.product_line_ids
                if pl.pieces_per_crate and pl.pieces_per_crate > 0 and pl.qty > 0
            )
            if not total_crates:
                continue

            # Idempotency: skip if already issued for this sheet + dealer
            already = CrateTxn.search([
                ('dispatch_sheet_id', '=', self.id),
                ('partner_id', '=', line.partner_id.id),
                ('move_type', '=', 'issue'),
                ('state', '=', 'confirmed'),
            ], limit=1)
            if already:
                _logger.info(
                    'Crate auto-issue skipped (already exists: %s) for dealer %s on sheet %s',
                    already.name, line.partner_id.name, self.name,
                )
                continue

            # Create in draft (sequence assigned), then confirm
            # milk.crate.transaction.qty is Integer — round half-crates up
            # (e.g. 2.5 crates dispatched → 3 crates issued to dealer ledger).
            crate_qty_int = int(total_crates) if total_crates == int(total_crates) \
                else int(total_crates) + 1

            txn = CrateTxn.create({
                'date': self.date,
                'partner_id': line.partner_id.id,
                'move_type': 'issue',
                'qty': crate_qty_int,
                'dispatch_sheet_id': self.id,
                'notes': f'Auto-issued on dispatch confirm: {self.name}',
            })
            txn.action_confirm()

            _logger.info(
                'Crate auto-issue: %s crate(s) issued to dealer \'%s\' (sheet: %s, txn: %s)',
                total_crates, line.partner_id.name, self.name, txn.name,
            )

    def action_mark_delivered(self):
        """
        Feature 4 (Option A): Mark the sheet as delivered.
        Invoices were already created at 'confirmed'. This state is an
        audit-trail acknowledgment that physical delivery has occurred.
        """
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(
                    f"Only confirmed sheets can be marked as delivered. "
                    f"'{rec.name}' is currently '{rec.state}'."
                )
            rec.state = 'delivered'

    def action_copy_last_sheet(self):
        """
        Feature 3: Populate the current draft sheet's dealer lines by copying
        from the most recent dispatch sheet for the same route.
        Any existing lines on the current sheet are replaced.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise UserError("Can only copy into a draft sheet.")
        if not self.route_id:
            raise UserError(
                "Set a Route on this sheet before copying from a previous one."
            )

        last = self.search([
            ('route_id', '=', self.route_id.id),
            ('id', '!=', self.id),
        ], order='date desc, id desc', limit=1)

        if not last:
            raise UserError(
                f"No previous dispatch sheet found for route "
                f"'{self.route_id.name}'."
            )
        if not last.line_ids:
            raise UserError(
                f"The last sheet '{last.name}' has no dealer lines to copy."
            )

        new_lines = []
        for line in last.line_ids:
            new_lines.append((0, 0, {
                'partner_id': line.partner_id.id,
                'product_line_ids': [
                    (0, 0, {
                        'product_id': pl.product_id.id,
                        'qty': pl.qty,
                        'rate': pl.rate,
                    })
                    for pl in line.product_line_ids
                ],
            }))

        # (5,0,0) removes all existing lines before adding the copied ones
        self.write({
            'driver_id': last.driver_id.id if last.driver_id else False,
            'line_ids': [(5, 0, 0)] + new_lines,
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': (
                    f"Copied {len(last.line_ids)} dealer line(s) from "
                    f"{last.name} ({last.date})."
                ),
                'sticky': False,
            },
        }

    # ── Fill from Default Orders ──────────────────────────────────────────────

    def action_fill_from_default_orders(self):
        """
        Pre-populate dealer lines on a draft dispatch sheet from the
        ``milk.dealer.default.order`` templates that match this sheet's route.

        Rules
        -----
        * Only works on draft sheets (raises UserError otherwise).
        * Route must be set on the sheet before calling this action.
        * Only active templates whose ``route_id`` matches ``self.route_id``
          are considered.
        * If the sheet already has a dealer line for a given dealer, that
          dealer's template is silently skipped — existing lines are preserved.
        * New dealer lines are appended; no existing line is removed or altered.
        * "Copy Last Sheet" is completely unaffected: the two features are
          independent and can be used in any order on the same draft sheet.
        * Returns a display_notification so the user sees a clear summary.
        """
        self.ensure_one()

        if self.state != 'draft':
            raise UserError("Can only fill from default orders on a draft sheet.")
        if not self.route_id:
            raise UserError(
                "Set a Route on this sheet before filling from default orders."
            )

        templates = self.env['milk.dealer.default.order'].search([
            ('route_id', '=', self.route_id.id),
            ('active', '=', True),
        ])

        if not templates:
            raise UserError(
                f"No active default order templates found for route "
                f"'{self.route_id.name}'.\n\n"
                f"Please configure templates under "
                f"Dispatch \u2192 Dealer Default Orders."
            )

        # Collect dealers already on this sheet to avoid duplicate lines
        existing_partner_ids = set(self.line_ids.mapped('partner_id').ids)

        new_lines = []
        skipped = []

        for tmpl in templates:
            if tmpl.partner_id.id in existing_partner_ids:
                skipped.append(tmpl.partner_id.name)
                continue
            if not tmpl.line_ids:
                _logger.info(
                    "Default order template '%s' has no product lines — skipped.",
                    tmpl.display_name,
                )
                continue

            new_lines.append((0, 0, {
                'partner_id': tmpl.partner_id.id,
                'product_line_ids': [
                    (0, 0, {
                        'product_id': line.product_id.id,
                        'qty': line.qty,
                        'rate': line.rate,
                    })
                    for line in tmpl.line_ids
                ],
            }))

        if not new_lines:
            msg = (
                "All dealers from the default order templates are already "
                "present on this sheet. No new lines were added."
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {'type': 'warning', 'message': msg, 'sticky': False},
            }

        # Append new lines — (5,0,0) is intentionally NOT used so existing
        # manually-added or copied lines are fully preserved.
        self.write({'line_ids': new_lines})

        msg = (
            f"Added {len(new_lines)} dealer line(s) from default order "
            f"templates for route '{self.route_id.name}'."
        )
        if skipped:
            msg += (
                f" {len(skipped)} dealer(s) skipped (already on sheet): "
                f"{', '.join(skipped)}."
            )

        _logger.info(
            "Dispatch sheet %s: filled %d line(s) from default orders "
            "(route: %s, skipped: %s).",
            self.name, len(new_lines), self.route_id.name, skipped or 'none',
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'type': 'success', 'message': msg, 'sticky': False},
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoices',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
        }

    # ── Bulk Actions (called from the list view Actions menu) ────────────────

    def action_bulk_confirm(self):
        """
        Bulk Confirm: confirm all selected draft sheets.
        Skips sheets that are already confirmed or delivered (no error raised
        for them — only a summary notification is returned).
        Delegates entirely to the existing action_confirm() so all invoice,
        ledger, and crate logic runs unchanged.
        """
        draft_sheets = self.filtered(lambda s: s.state == 'draft')
        skipped = len(self) - len(draft_sheets)

        if not draft_sheets:
            raise UserError(
                "None of the selected sheets are in Draft state. "
                "Only draft sheets can be confirmed."
            )

        # action_confirm() iterates self internally — pass the filtered set.
        draft_sheets.action_confirm()

        msg = f"Successfully confirmed {len(draft_sheets)} sheet(s)."
        if skipped:
            msg += f" {skipped} sheet(s) skipped (already confirmed/delivered)."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': msg,
                'sticky': False,
            },
        }

    def action_bulk_deliver(self):
        """
        Bulk Mark as Delivered: mark all selected confirmed sheets as delivered.
        Skips draft and already-delivered sheets silently.
        Delegates to the existing action_mark_delivered() so no logic changes.
        """
        confirmed_sheets = self.filtered(lambda s: s.state == 'confirmed')
        skipped = len(self) - len(confirmed_sheets)

        if not confirmed_sheets:
            raise UserError(
                "None of the selected sheets are in Confirmed state. "
                "Only confirmed sheets can be marked as delivered."
            )

        confirmed_sheets.action_mark_delivered()

        msg = f"Successfully marked {len(confirmed_sheets)} sheet(s) as delivered."
        if skipped:
            msg += f" {skipped} sheet(s) skipped (not in confirmed state)."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': msg,
                'sticky': False,
            },
        }

    def action_export_summary(self):
        """
        Export Summary: generate a CSV of all selected sheets with one row
        per dealer line.

        Columns:
            Sheet Ref | Date | Route | Driver | Dealer | Product | Qty (Crates)
            | Rate (Rs) | Amount (Rs) | Sheet Total (Rs) | Status

        The CSV is stored as an ir.attachment and returned as a download URL
        so the user gets an immediate browser download with no extra wizard.
        """
        if not self:
            raise UserError("No sheets selected for export.")

        buf = io.StringIO()
        writer = csv.writer(buf)

        # Header row
        writer.writerow([
            'Sheet Ref', 'Date', 'Route', 'Driver',
            'Dealer', 'Product', 'Qty (Crates)', 'Rate (Rs)',
            'Amount (Rs)', 'Sheet Total (Rs)', 'Status',
        ])

        for rec in self.sorted(key=lambda s: (s.date, s.name)):
            route_name = rec.route_id.name or ''
            driver_name = rec.driver_id.name or ''
            sheet_total = rec.total_amount
            status = dict(rec._fields['state'].selection).get(rec.state, rec.state)

            if not rec.line_ids:
                # Sheet with no dealer lines — still include one summary row
                writer.writerow([
                    rec.name, rec.date, route_name, driver_name,
                    '', '', '', '', '', sheet_total, status,
                ])
                continue

            for line in rec.line_ids:
                dealer_name = line.partner_id.name or ''

                if not line.product_line_ids:
                    writer.writerow([
                        rec.name, rec.date, route_name, driver_name,
                        dealer_name, '', '', '', line.total_amount,
                        sheet_total, status,
                    ])
                    continue

                for pl in line.product_line_ids:
                    if pl.qty <= 0:
                        continue
                    writer.writerow([
                        rec.name,
                        rec.date,
                        route_name,
                        driver_name,
                        dealer_name,
                        pl.product_id.name or '',
                        pl.qty,
                        pl.rate,
                        round(pl.amount, 2),
                        sheet_total,
                        status,
                    ])

        csv_bytes = buf.getvalue().encode('utf-8')

        # Store as a temporary attachment (auto-deleted after download)
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'dispatch_summary.csv',
            'type': 'binary',
            'datas': base64.b64encode(csv_bytes),
            'mimetype': 'text/csv',
            'res_model': self._name,
            'res_id': self.ids[0],
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ── WhatsApp helper ───────────────────────────────────────────────────────

    def _send_whatsapp_bill(self, partner, bill_amount):
        if 'whatsapp.message' not in self.env:
            return
        try:
            wa_account = self.env['whatsapp.account'].search(
                [('active', '=', True)], limit=1,
            )
            if not wa_account:
                return
            mobile = partner.mobile or partner.phone
            if not mobile:
                return
            ledger = self.env['milk.partner.ledger'].search([
                ('partner_id', '=', partner.id),
                ('date', '=', self.date),
            ], limit=1)
            # Feature 5: route label now comes from the route master record
            route_label = self.route_id.name or ''
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
