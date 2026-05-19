import logging
from odoo import models, api

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Products whose internal_reference (or name) contains any of these tokens
# are treated as BOX products (Section B of the driver sheet).
# ---------------------------------------------------------------------------
_BOX_KEYWORDS = frozenset({
    'box', 'curd', 'dahi', 'lassi', 'paneer', 'shrikhand',
})


def _is_box_product(product):
    """Return True when a product belongs to the BOX section."""
    ref = (product.default_code or '').lower()
    name = (product.name or '').lower()
    return any(kw in ref or kw in name for kw in _BOX_KEYWORDS)


def _short_name(product):
    """Column header for a product: internal_reference or first 10 chars of name."""
    ref = (product.default_code or '').strip()
    return ref if ref else (product.name or '')[:10]


class ReportDriverSheet(models.AbstractModel):
    """
    Abstract model for QWeb template ``milk_distribution_management.report_driver``.

    CHANGE (route-grouped printing):
    - report_data now keyed by route_id instead of doc.id
    - Each route produces ONE set of tables with ALL dealers of that route
    - All dealers for the same route appear on a single page/section
    - No change to dispatch sheet model, workflow, or business logic
    """
    _name = 'report.milk_distribution_management.report_driver'
    _description = 'Driver Sheet PDF Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['milk.dispatch.sheet'].browse(docids)

        # ── Group selected dispatch sheets by route ──────────────────────────
        # Multiple dispatch sheets with the same route on the same day
        # are merged so all dealers appear on one page per route.
        route_docs = {}  # route_id -> list of dispatch sheet records
        for doc in docs:
            rid = doc.route_id.id if doc.route_id else 0
            if rid not in route_docs:
                route_docs[rid] = {
                    'route': doc.route_id,
                    'driver': doc.driver_id,
                    'date': doc.date,
                    'ref': doc.name,
                    'sheets': [],
                }
            route_docs[rid]['sheets'].append(doc)

        # ── Build report data per route ──────────────────────────────────────
        report_data = {}  # route_id -> {products, rows, totals, ...}

        for rid, info in route_docs.items():
            all_products = {}  # product.id -> product record
            all_lines = []  # flattened dispatch lines across all sheets

            for sheet in info['sheets']:
                for line in sheet.line_ids:
                    for pl in line.product_line_ids:
                        if pl.qty > 0 and pl.product_id:
                            all_products[pl.product_id.id] = pl.product_id
                    all_lines.append(line)

            # ── Split into regular vs box, sorted by sequence then name ──────
            regular_prods = []
            box_prods = []
            for prod in sorted(all_products.values(),
                               key=lambda p: (p.sequence or 999, p.name or '')):
                if _is_box_product(prod):
                    box_prods.append({'id': prod.id, 'short_name': _short_name(prod)})
                else:
                    regular_prods.append({'id': prod.id, 'short_name': _short_name(prod)})

            reg_ids = [p['id'] for p in regular_prods]
            box_ids = [p['id'] for p in box_prods]

            # ── Build rows for each dealer line (all sheets merged) ──────────
            regular_rows = []
            box_rows = []

            for line in all_lines:
                if line.total_amount <= 0:
                    continue

                qty_map = {pl.product_id.id: pl.qty
                           for pl in line.product_line_ids if pl.qty > 0}
                amt_map = {pl.product_id.id: pl.amount
                           for pl in line.product_line_ids if pl.qty > 0}

                if any(pid in qty_map for pid in reg_ids):
                    reg_qtys = [qty_map.get(pid, 0) or None for pid in reg_ids]
                    reg_total = sum(amt_map.get(pid, 0) for pid in reg_ids)
                    regular_rows.append({
                        'dealer_name': line.partner_id.name or '',
                        'qtys': reg_qtys,
                        'total': reg_total,
                    })

                if any(pid in qty_map for pid in box_ids):
                    box_qtys = [qty_map.get(pid, 0) or None for pid in box_ids]
                    box_total = sum(amt_map.get(pid, 0) for pid in box_ids)
                    box_rows.append({
                        'dealer_name': line.partner_id.name or '',
                        'qtys': box_qtys,
                        'total': box_total,
                    })

            # ── Column totals ────────────────────────────────────────────────
            def _col_totals(rows, n_cols):
                totals = [0.0] * n_cols
                for row in rows:
                    for i, q in enumerate(row['qtys']):
                        totals[i] += (q or 0)
                return [t if t else None for t in totals]

            reg_col_totals = _col_totals(regular_rows, len(reg_ids))
            box_col_totals = _col_totals(box_rows, len(box_ids))

            regular_grand_total = sum(r['total'] for r in regular_rows)
            box_grand_total = sum(r['total'] for r in box_rows)

            # Multi-sheet ref: comma-separated if more than one sheet
            refs = ', '.join(s.name for s in info['sheets'])

            report_data[rid] = {
                'route': info['route'],
                'driver': info['driver'],
                'date': info['date'],
                'ref': refs,
                'regular_products': regular_prods,
                'regular_rows': regular_rows,
                'regular_col_totals': reg_col_totals,
                'regular_grand_total': regular_grand_total,
                'box_products': box_prods,
                'box_rows': box_rows,
                'box_col_totals': box_col_totals,
                'box_grand_total': box_grand_total,
            }

        # route_keys preserves insertion order for template iteration
        route_keys = list(route_docs.keys())

        return {
            'docs': docs,
            'report_data': report_data,
            'route_keys': route_keys,
        }
