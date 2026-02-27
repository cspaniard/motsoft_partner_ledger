# -*- coding: utf-8 -*-

from odoo import models, fields, api


class MotsoftPartnerLedger(models.TransientModel):
    _name = 'motsoft.partner.ledger'
    _description = "Libro Mayor Empresa"

    company_name = fields.Char(default=lambda self: self.env.user.company_id.name, readonly=True)

    date_start = fields.Date(string="Fecha de inicio", default='2022-01-01')
    date_end = fields.Date(string="Fecha final", default='2022-12-31')
    tax_box = fields.Selection([
        ('237', '237'),
        ('242', '242'),
        ('249', '249')],
        string='Casilla:'
    )

    account_filter = fields.Char(string='Buscar Cuentas:')

    account_ids = fields.Many2many(
        comodel_name='account.account',
        string='Cuentas Contables'
    )

    @api.onchange('tax_box')
    def _onchange_tax_box(self):
        """Actualizar cuentas según la casilla seleccionada."""
        if not self.tax_box:
            self.account_ids = [(5, 0, 0)]  # limpia el campo
            return

        mapping = {
            '237': ['551001', '551018', '551019', '551020'],
            '242': ['400000'],
            '249': ['410000', '465000', '475000', '475100', '476000'],
        }

        codes = mapping.get(self.tax_box, [])
        accounts = self.env['account.account'].search([('code', 'in', codes)])
        self.account_ids = accounts

    def search_accounts(self):
        self.tax_box = None

        accounts = self.env['account.account'].search([('code', '=like', self.account_filter + '%')])
        self.account_ids += accounts

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def clear_accounts(self):
        self.account_ids = [(5, 0, 0)]  # limpia el campo
        self.tax_box = None

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_print(self):

        return self.env.ref(
            'motsoft_partner_ledger.partner_ledger_report'
        ).report_action(self)
