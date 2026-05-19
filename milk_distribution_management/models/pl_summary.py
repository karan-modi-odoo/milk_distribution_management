import logging
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class MilkPlSummary(models.TransientModel):
    """
    Distributor P&L Summary for a selected date range.

    Data sources (read-only, no writes to any existing model):
        milk.dispatch.sheet   state IN (confirmed, delivered)  → Total Dealer Billing
        milk.dairy.purchase   state = confirmed                → Dairy Purchase Cost
        milk.crate.billing    state = approved, date_to in range → Crate Rental Income
        Driver Expenses       not tracked yet                  → 0.00 placeholder

    Computed:
        Gross Margin = Total Dealer Billing − Dairy Purchase Cost
        Net Profit   = Gross Margin − Driver Expenses + Crate Rental Income
    """
    _name = 'milk.pl.summary'
    _description = 'Distributor P&L Summary'

    # ── Inputs ────────────────────────────────────────────────────────────────
    date_from = fields.Date(
        string='From Date', required=True, default=fields.Date.today,
    )
    date_to = fields.Date(
        string='To Date', required=True, default=fields.Date.today,
    )

    # ── P&L lines (written by action_generate, never by user) ─────────────────
    total_dealer_billing = fields.Float(
        string='Total Dealer Billing (Rs)', digits=(16, 2), readonly=True,
    )
    dairy_purchase_cost = fields.Float(
        string='Dairy Purchase Cost (Rs)', digits=(16, 2), readonly=True,
    )
    gross_margin = fields.Float(
        string='Gross Margin (Rs)', digits=(16, 2), readonly=True,
    )
    driver_expenses = fields.Float(
        string='Driver Expenses (Rs)', digits=(16, 2), readonly=True,
        help='Not tracked yet. Placeholder for a future driver expense model.',
    )
    crate_income = fields.Float(
        string='Crate Rental Income (Rs)', digits=(16, 2), readonly=True,
    )
    net_profit = fields.Float(
        string='Net Profit (Rs)', digits=(16, 2), readonly=True,
    )

    # ── Data-source counts (traceability only) ────────────────────────────────
    dispatch_sheet_count = fields.Integer(
        string='Dispatch Sheets', readonly=True,
    )
    dairy_purchase_count = fields.Integer(
        string='Dairy Purchase Bills', readonly=True,
    )
    crate_billing_count = fields.Integer(
        string='Crate Billing Records', readonly=True,
    )

    # ── Generated flag ────────────────────────────────────────────────────────
    is_generated = fields.Boolean(default=False)

    # ── Constraints ───────────────────────────────────────────────────────────

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from > rec.date_to:
                raise ValidationError(
                    "'From Date' must be on or before 'To Date'."
                )

    # ── Reset results when dates change ───────────────────────────────────────

    @api.onchange('date_from', 'date_to')
    def _onchange_dates(self):
        """Hide stale results if the user changes the date range after generating."""
        self.is_generated = False

    # ── Generate ──────────────────────────────────────────────────────────────

    def action_generate(self):
        self.ensure_one()

        # 1. Total Dealer Billing
        sheets = self.env['milk.dispatch.sheet'].search([
            ('state', 'in', ('confirmed', 'delivered')),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ])
        total_billing = sum(sheets.mapped('total_amount'))

        # 2. Dairy Purchase Cost
        purchases = self.env['milk.dairy.purchase'].search([
            ('state', '=', 'confirmed'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ])
        total_cost = sum(purchases.mapped('total_amount'))

        # 3. Crate Rental Income
        #    Filter by date_to of the billing period falling within report range,
        #    since date_to is when the billing is finalised and charged.
        crate_billings = self.env['milk.crate.billing'].search([
            ('state', '=', 'approved'),
            ('date_to', '>=', self.date_from),
            ('date_to', '<=', self.date_to),
        ])
        total_crate = sum(crate_billings.mapped('total_charge'))

        # 4. Computed lines
        gross_margin = total_billing - total_cost
        driver_expenses = 0.0  # placeholder — no model exists yet
        net_profit = gross_margin - driver_expenses + total_crate

        self.write({
            'total_dealer_billing': total_billing,
            'dairy_purchase_cost': total_cost,
            'gross_margin': gross_margin,
            'driver_expenses': driver_expenses,
            'crate_income': total_crate,
            'net_profit': net_profit,
            'dispatch_sheet_count': len(sheets),
            'dairy_purchase_count': len(purchases),
            'crate_billing_count': len(crate_billings),
            'is_generated': True,
        })

        _logger.info(
            "P&L Summary generated: %s to %s | Billing=%.2f Cost=%.2f "
            "GrossMargin=%.2f CrateIncome=%.2f NetProfit=%.2f",
            self.date_from, self.date_to,
            total_billing, total_cost, gross_margin, total_crate, net_profit,
        )

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'milk.pl.summary',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print(self):
        self.ensure_one()
        if not self.is_generated:
            raise UserError("Generate the report first before printing.")
        return self.env.ref(
            'milk_distribution_management.action_report_pl_summary'
        ).report_action(self)


# ── Abstract model for QWeb PDF ───────────────────────────────────────────────
class ReportPlSummary(models.AbstractModel):
    _name = 'report.milk_distribution_management.report_pl_summary'
    _description = 'Distributor P&L Summary PDF'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.pl.summary'].browse(docids)
        return {'docs': docs, 'company': self.env.company}
