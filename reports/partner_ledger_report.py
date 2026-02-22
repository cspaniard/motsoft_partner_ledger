from odoo import models, api
from odoo.tools.misc import formatLang

class ReportPartnerLedger(models.AbstractModel):

    # Convención OBLIGATORIA: debe llamarse 'report.model_name.' + report_name del ir.actions.report
    _name = 'report.motsoft_partner_ledger.partner_ledger_template'
    _description = 'Informe Libro Mayor Empresa'

    @api.model
    def _get_report_values(self, docids, data=None):
        # docids contiene el ID del wizard porque usas report_action(self)
        if data and data.get('docids'):
            docids = data['docids']

        # Recuperamos el registro del wizard con sus valores
        wizard = self.env['motsoft.partner.ledger'].browse(docids)

        # Ejecutamos nuestra consulta SQL con los parámetros del wizard
        partners = self.get_partners_by_account(
            wizard.date_start,
            wizard.date_end,
            wizard.account_ids
        )

        return {
            'doc_ids':      docids,
            'doc_model':    'motsoft.partner.ledger',
            'docs':         wizard,         # el registro wizard (opcional, para acceder a sus campos)
            'partners':     partners,        # nuestra data de la consulta SQL
            'formatLang':   formatLang,   # <-- lo inyectamos aquí
        }

    # def get_partners_by_account(self, date_start, date_end, account_ids):
    #
    #     account_codes = account_ids.mapped('code')
    #
    #     query = """
    #             SELECT aa.code as account_code, rp.id AS partner_id, rp.name, SUM(balance) AS balance
    #             FROM account_move_line AS aml
    #                      JOIN account_account AS aa ON aml.account_id = aa.id
    #                      JOIN res_partner AS rp ON aml.partner_id = rp.id
    #             WHERE aa.code IN %s
    #               AND aml.date >= %s
    #               AND aml.date <= %s
    #             GROUP BY aa.code, rp.id, rp.name
    #             HAVING SUM(balance) <> 0.0
    #             ORDER BY aa.code, rp.name \
    #             """
    #
    #     self.env.cr.execute(query, [tuple(account_codes), date_start, date_end])
    #     return self.env.cr.dictfetchall()

    def get_partners_by_account(self, date_start, date_end, account_ids):
        account_codes = account_ids.mapped('code')

        # ── 1. Consulta resumen (igual que antes) ──────────────────────────
        query_summary = """
            SELECT aa.code AS account_code,
                   rp.id   AS partner_id,
                   rp.name,
                   SUM(balance) AS balance
            FROM account_move_line AS aml
                JOIN account_account AS aa ON aml.account_id = aa.id
                JOIN res_partner     AS rp ON aml.partner_id = rp.id
            WHERE aa.code IN %s
              AND aml.date >= %s
              AND aml.date <= %s
            GROUP BY aa.code, rp.id, rp.name
            HAVING SUM(balance) <> 0.0
            ORDER BY aa.code, rp.name
        """
        self.env.cr.execute(query_summary, [tuple(account_codes), date_start, date_end])
        partners = self.env.cr.dictfetchall()

        # ── 2. Consulta detalle para TODOS los pares (account_code, partner_id)
        #       en una sola llamada a la BD, luego se distribuye en Python ──
        if not partners:
            return partners

        # Construimos un filtro multi-valor eficiente
        pairs = [(p['account_code'], p['partner_id']) for p in partners]

        query_detail = """
            SELECT aa.code  AS account_code,
                   aml.partner_id,
                   aml.id,
                   aml.date,
                   aml.ref,
                   aml.debit,
                   aml.credit,
                   SUM(aml.debit - aml.credit) OVER (
                       PARTITION BY aa.code, aml.partner_id          -- saldo acumulado independiente por cada par
                       ORDER BY aml.date, aml.id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS balance_acc
            FROM account_move_line AS aml
                JOIN account_account AS aa ON aml.account_id = aa.id
            WHERE aa.code IN %s
              AND aml.date >= %s
              AND aml.date <= %s
              AND aml.partner_id IN %s
            ORDER BY aa.code, aml.partner_id, aml.date, aml.id
        """
        partner_ids = list({p['partner_id'] for p in partners})
        self.env.cr.execute(
            query_detail,
            [tuple(account_codes), date_start, date_end, tuple(partner_ids)]
        )
        detail_rows = self.env.cr.dictfetchall()

        # ── 3. Indexamos los detalles por (account_code, partner_id) ──────
        from collections import defaultdict
        detail_index = defaultdict(list)
        for row in detail_rows:
            key = (row['account_code'], row['partner_id'])
            detail_index[key].append(row)

        # ── 4. Inyectamos los detalles en cada registro resumen ───────────
        for partner in partners:
            key = (partner['account_code'], partner['partner_id'])
            partner['lines'] = detail_index.get(key, [])

        return partners
