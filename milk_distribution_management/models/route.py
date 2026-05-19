from odoo import models, fields, api
from odoo.exceptions import ValidationError


class MilkRoute(models.Model):
    """
    Configurable delivery route master.
    Replaces the hardcoded ROUTE_SELECTION on milk.dispatch.sheet so that
    new routes (or route renames) need no code change.
    """
    _name = 'milk.route'
    _description = 'Milk Delivery Route'
    _order = 'name'
    _rec_name = 'name'

    name = fields.Char(string='Route Name', required=True)
    active = fields.Boolean(default=True)

    @api.constrains('name')
    def _check_unique_name(self):
        for rec in self:
            if self.search_count([
                ('name', '=', rec.name),
                ('id', '!=', rec.id),
            ]) > 0:
                raise ValidationError("Route name must be unique.")
