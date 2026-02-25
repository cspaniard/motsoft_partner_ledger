# -*- coding: utf-8 -*-

from odoo import models, fields, api


class MotsoftPartnerLedger(models.TransientModel):
    _name = 'motsoft.partner.ledger'
    _description = "Libro Mayor Empresa"

    company_name = fields.Char(default=lambda self: self.env.user.company_id.name, readonly=True)

    date_start = fields.Date(string="Fecha de inicio", default='2022-01-01')
    date_end = fields.Date(string="Fecha final", default='2022-12-31')

    account_ids = fields.Many2many(
        comodel_name='account.account',
        string='Cuentas Contables',
        default=lambda self: self.env['account.account'].search([
            ('code', 'in', ['400000',    # 242
                            '410000', '465000', '475000', '475100', '476000', # 249
                            '551001', '551018', '551019', '551020']),  # 237
        ])
    )

    def action_print(self):

        return self.env.ref(
            'motsoft_partner_ledger.partner_ledger_report'
        ).report_action(self)
