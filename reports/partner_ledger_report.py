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

    def get_partners_by_account(self, date_start, date_end, account_ids):
        account_codes = account_ids.mapped('code')

        # ── 1. Consulta resumen (igual que antes) ──────────────────────────
        query_summary = """
            WITH
            -- Previous balance: everything before date_from
            previous_balance AS (
                SELECT aml.account_id,
                       CASE WHEN aa.code IN %s THEN NULL ELSE aml.partner_id END AS partner_id,
                       SUM(aml.balance) AS balance
                FROM account_move_line AS aml
                    JOIN account_account AS aa ON aml.account_id = aa.id
                    JOIN account_move    AS am ON aml.move_id    = am.id
                WHERE aa.code IN %s
                  AND aml.date < %s
                  AND (aml.partner_id IS NOT NULL OR aa.code IN %s)
                  AND am.state = 'posted'
                GROUP BY aml.account_id,
                         CASE WHEN aa.code IN %s THEN NULL ELSE aml.partner_id END
            ),
            -- Balance for the requested period
            period_balance AS (
                SELECT aa.code  AS account_code,
                       aa.id    AS account_id,
                       CASE WHEN aa.code IN %s THEN NULL ELSE rp.id   END AS partner_id,
                       CASE WHEN aa.code IN %s THEN NULL ELSE rp.name END AS name,
                       SUM(aml.balance) AS balance
                FROM account_move_line AS aml
                    JOIN account_account AS aa ON aml.account_id = aa.id
                    LEFT JOIN res_partner AS rp ON aml.partner_id = rp.id
                    JOIN account_move    AS am ON aml.move_id    = am.id
                WHERE aa.code IN %s
                  AND aml.date >= %s
                  AND aml.date <= %s
                  AND (aml.partner_id IS NOT NULL OR aa.code IN %s)
                  AND am.state = 'posted'
                GROUP BY aa.code, aa.id,
                         CASE WHEN aa.code IN %s THEN NULL ELSE rp.id   END,
                         CASE WHEN aa.code IN %s THEN NULL ELSE rp.name END
            ),
            -- Merge both to avoid losing partners with previous balance but no movements in the period
            all_balances AS (
                SELECT pb.account_code,
                       pb.account_id,
                       pb.partner_id,
                       pb.name,
                       COALESCE(prev.balance, 0) AS previous_balance,
                       COALESCE(pb.balance,   0) AS period_balance
                FROM period_balance AS pb
                LEFT JOIN previous_balance AS prev ON prev.account_id = pb.account_id
                                                  AND (prev.partner_id = pb.partner_id
                                                       OR (prev.partner_id IS NULL AND pb.partner_id IS NULL))
                UNION ALL
                -- Partners with previous balance but no movements in the period
                SELECT aa.code AS account_code,
                       prev.account_id,
                       prev.partner_id,
                       rp.name,
                       prev.balance AS previous_balance,
                       0            AS period_balance
                FROM previous_balance AS prev
                    JOIN account_account AS aa ON prev.account_id = aa.id
                    LEFT JOIN res_partner AS rp ON prev.partner_id = rp.id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM period_balance AS pb
                    WHERE pb.account_id = prev.account_id
                      AND (pb.partner_id = prev.partner_id
                           OR (pb.partner_id IS NULL AND prev.partner_id IS NULL))
                )
            )
            SELECT account_code,
                   partner_id,
                   name,
                   previous_balance,
                   period_balance,
                   previous_balance + period_balance AS total_balance
            FROM all_balances
            WHERE ABS(previous_balance + period_balance) > 0.001
            ORDER BY account_code, name            
        """
        SPECIAL_ACCOUNTS = ('475000', '475100')

        self.env.cr.execute(query_summary, [
            tuple(SPECIAL_ACCOUNTS),  # 1 - CASE WHEN previous_balance SELECT
            tuple(account_codes),  # 2 - WHERE aa.code IN (previous_balance)
            date_start,  # 3 - aml.date
            tuple(SPECIAL_ACCOUNTS),  # 4 - OR aa.code IN (previous_balance WHERE)
            tuple(SPECIAL_ACCOUNTS),  # 5 - CASE WHEN previous_balance GROUP BY
            tuple(SPECIAL_ACCOUNTS),  # 6 - CASE WHEN period_balance SELECT partner_id
            tuple(SPECIAL_ACCOUNTS),  # 7 - CASE WHEN period_balance SELECT name
            tuple(account_codes),  # 8 - WHERE aa.code IN (period_balance)
            date_start,  # 9 - aml.date >=
            date_end,  # 10 - aml.date <=
            tuple(SPECIAL_ACCOUNTS),  # 11 - OR aa.code IN (period_balance WHERE)
            tuple(SPECIAL_ACCOUNTS),  # 12 - CASE WHEN period_balance GROUP BY partner_id
            tuple(SPECIAL_ACCOUNTS),  # 13 - CASE WHEN period_balance GROUP BY name
        ])
        partners = self.env.cr.dictfetchall()

        # ── 2. Consulta detalle para TODOS los pares (account_code, partner_id)
        #       en una sola llamada a la BD, luego se distribuye en Python ──
        if not partners:
            return partners

        # Construimos un filtro multi-valor eficiente
        # pairs = [(p['account_code'], p['partner_id']) for p in partners]

        # ── 2. Preparamos los partner_ids para el detalle ─────────────────────
        # Los None (cuentas especiales) no pueden ir en IN %s de SQL
        partner_ids = list({
            p['partner_id']
            for p in partners
            if p['partner_id'] is not None
        })
        # Fallback: si solo hay cuentas especiales, el IN %s nunca matcheará
        # pero la rama OR aa.code IN %s sí traerá esas líneas
        safe_partner_ids = tuple(partner_ids) if partner_ids else (0,)

        query_detail = """
            WITH
            -- Previous balance per account + partner
            previous_balance AS (
                SELECT aml.account_id,
                       CASE WHEN aa.code IN %s THEN NULL ELSE aml.partner_id END AS partner_id,
                       SUM(aml.debit - aml.credit) AS balance
                FROM account_move_line AS aml
                    JOIN account_account AS aa ON aml.account_id = aa.id
                    JOIN account_move    AS am ON aml.move_id    = am.id
                WHERE aa.code IN %s
                  AND aml.date < %s
                  AND (
                        (aa.code NOT IN %s AND aml.partner_id IN %s)
                        OR
                        (aa.code IN %s)
                      )
                  AND am.state = 'posted'
                GROUP BY aml.account_id,
                         CASE WHEN aa.code IN %s THEN NULL ELSE aml.partner_id END
            ),
            -- Period lines as real rows
            period_lines AS (
                SELECT aa.code AS account_code,
                       aa.id   AS account_id,
                       CASE WHEN aa.code IN %s THEN NULL ELSE aml.partner_id END AS partner_id,
                       aml.id,
                       aml.date,
                       aml.ref,
                       aml.debit,
                       aml.credit
                FROM account_move_line AS aml
                    JOIN account_account AS aa ON aml.account_id = aa.id
                    JOIN account_move    AS am ON aml.move_id    = am.id
                WHERE aa.code IN %s
                  AND aml.date >= %s
                  AND aml.date <= %s
                  AND (
                        (aa.code NOT IN %s AND aml.partner_id IN %s)
                        OR
                        (aa.code IN %s)
                      )
                  AND am.state = 'posted'
            ),
            -- Synthetic "Previous balance" row per account + partner combination
            previous_balance_row AS (
                SELECT aa.code  AS account_code,
                       prev.account_id,
                       prev.partner_id,
                       -1         AS id,
                       NULL::date AS date,
                       'Asiento Apertura' AS ref,
                       CASE WHEN prev.balance > 0 THEN  prev.balance ELSE 0 END AS debit,
                       CASE WHEN prev.balance < 0 THEN -prev.balance ELSE 0 END AS credit
                FROM previous_balance AS prev
                    JOIN account_account AS aa ON prev.account_id = aa.id
                WHERE ABS(prev.balance) > 0.001
            ),
            -- Union: previous balance row first, then period lines
            all_lines AS (
                SELECT account_code, account_id, partner_id, id, date, ref, debit, credit, 0 AS sort_order
                FROM previous_balance_row
                UNION ALL
                SELECT account_code, account_id, partner_id, id, date, ref, debit, credit, 1 AS sort_order
                FROM period_lines
            )
            SELECT account_code,
                   partner_id,
                   id,
                   date,
                   ref,
                   debit,
                   credit,
                   SUM(debit - credit) OVER (
                       PARTITION BY account_code, partner_id
                       ORDER BY sort_order, date, id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS balance_acc
            FROM all_lines
            ORDER BY account_code, partner_id, sort_order, date, id            
        """

        self.env.cr.execute(query_detail, [
            tuple(SPECIAL_ACCOUNTS),  # 1 - CASE WHEN previous_balance SELECT
            tuple(account_codes),  # 2 - WHERE aa.code IN (previous_balance)
            date_start,  # 3 - aml.date
            tuple(SPECIAL_ACCOUNTS),  # 4 - aa.code NOT IN (previous_balance WHERE)
            safe_partner_ids,  # 5 - aml.partner_id IN (previous_balance WHERE)
            tuple(SPECIAL_ACCOUNTS),  # 6 - aa.code IN OR (previous_balance WHERE)
            tuple(SPECIAL_ACCOUNTS),  # 7 - CASE WHEN previous_balance GROUP BY
            tuple(SPECIAL_ACCOUNTS),  # 8 - CASE WHEN period_lines SELECT
            tuple(account_codes),  # 9 - WHERE aa.code IN (period_lines)
            date_start,  # 10 - aml.date >=
            date_end,  # 11 - aml.date <=
            tuple(SPECIAL_ACCOUNTS),  # 12 - aa.code NOT IN (period_lines WHERE)
            safe_partner_ids,  # 13 - aml.partner_id IN (period_lines WHERE)
            tuple(SPECIAL_ACCOUNTS),  # 14 - aa.code IN OR (period_lines WHERE)
        ])

        detail_rows = self.env.cr.dictfetchall()

        # ── Tras obtener partners y detail_rows ──────────────────────────────

        # Cargamos los nombres de las cuentas especiales desde Odoo
        special_account_names = {
            aa.code: aa.name
            for aa in self.env['account.account'].search([('code', 'in', list(SPECIAL_ACCOUNTS))])
        }

        # En el resumen: si partner_id es None, usamos el nombre de la cuenta
        for partner in partners:
            if partner['partner_id'] is None:
                partner['name'] = special_account_names.get(partner['account_code'])

        # En el detalle: igual, propagamos antes de indexar
        for row in detail_rows:
            if row['partner_id'] is None:
                row['name'] = special_account_names.get(row['account_code'])

        # ── 4. Indexamos por (account_code, partner_id) — None es clave válida ──
        from collections import defaultdict
        detail_index = defaultdict(list)
        for row in detail_rows:
            key = (row['account_code'], row['partner_id'])
            detail_index[key].append(row)

        # ── 5. Inyectamos los detalles en cada registro resumen ───────────────
        for partner in partners:
            key = (partner['account_code'], partner['partner_id'])
            partner['lines'] = detail_index.get(key, [])

        return partners
