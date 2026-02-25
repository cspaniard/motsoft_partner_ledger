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
                       aml.partner_id,
                       SUM(aml.balance) AS balance
                FROM account_move_line AS aml
                    JOIN account_account AS aa ON aml.account_id = aa.id
                    JOIN account_move    AS am ON aml.move_id    = am.id
                WHERE aa.code IN %s
                  AND aml.date < %s
                  AND aml.partner_id IS NOT NULL
                  AND am.state = 'posted'
                GROUP BY aml.account_id, aml.partner_id
            ),

            -- Balance for the requested period
            period_balance AS (
                SELECT aa.code  AS account_code,
                       aa.id    AS account_id,
                       rp.id    AS partner_id,
                       rp.name,
                       SUM(aml.balance) AS balance
                FROM account_move_line AS aml
                    JOIN account_account AS aa ON aml.account_id = aa.id
                    JOIN res_partner     AS rp ON aml.partner_id = rp.id
                    JOIN account_move    AS am ON aml.move_id    = am.id
                WHERE aa.code IN %s
                  AND aml.date >= %s
                  AND aml.date <= %s
                  AND aml.partner_id IS NOT NULL
                  AND am.state = 'posted'
                GROUP BY aa.code, aa.id, rp.id, rp.name
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
                                                  AND prev.partner_id = pb.partner_id

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
                    JOIN res_partner     AS rp ON prev.partner_id = rp.id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM period_balance AS pb
                    WHERE pb.account_id = prev.account_id
                      AND pb.partner_id = prev.partner_id
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
        self.env.cr.execute(query_summary, [
            tuple(account_codes),  # IN %s  (previous_balance)
            date_start,            # < %s   (previous_balance)
            tuple(account_codes),  # IN %s  (period_balance)
            date_start,            # >= %s  (period_balance)
            date_end,              # <= %s  (period_balance)
        ])
        partners = self.env.cr.dictfetchall()

        # ── 2. Consulta detalle para TODOS los pares (account_code, partner_id)
        #       en una sola llamada a la BD, luego se distribuye en Python ──
        if not partners:
            return partners

        # Construimos un filtro multi-valor eficiente
        pairs = [(p['account_code'], p['partner_id']) for p in partners]

        query_detail = """
            WITH
            -- Previous balance per account + partner
            previous_balance AS (
                SELECT aml.account_id,
                       aml.partner_id,
                       SUM(aml.debit - aml.credit) AS balance
                FROM account_move_line AS aml
                    JOIN account_account AS aa ON aml.account_id = aa.id
                    JOIN account_move    AS am ON aml.move_id    = am.id
                WHERE aa.code IN %s
                  AND aml.date < %s
                  AND aml.partner_id IN %s
                  AND am.state = 'posted'
                GROUP BY aml.account_id, aml.partner_id
            ),

            -- Period lines as real rows
            period_lines AS (
                SELECT aa.code AS account_code,
                       aa.id   AS account_id,
                       aml.partner_id,
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
                  AND aml.partner_id IN %s
                  AND am.state = 'posted'
            ),

            -- Synthetic "Previous balance" row per account + partner combination
            previous_balance_row AS (
                SELECT aa.code  AS account_code,
                       prev.account_id,
                       prev.partner_id,
                       -1       AS id,
                       NULL::date AS date,
                       'Asiento Apertura' AS ref,
                       CASE WHEN prev.balance > 0 THEN  prev.balance ELSE 0 END AS debit,
                       CASE WHEN prev.balance < 0 THEN -prev.balance ELSE 0 END AS credit
                FROM previous_balance AS prev
                    JOIN account_account AS aa ON prev.account_id = aa.id
                -- Uncomment to suppress the row when previous balance is exactly zero:
                WHERE ABS(prev.balance) > 0.001
            ),

            -- Union: previous balance row first, then period lines
            all_lines AS (
                SELECT account_code,
                       account_id,
                       partner_id,
                       id,
                       date,
                       ref,
                       debit,
                       credit,
                       0 AS sort_order
                FROM previous_balance_row

                UNION ALL

                SELECT account_code,
                       account_id,
                       partner_id,
                       id,
                       date,
                       ref,
                       debit,
                       credit,
                       1 AS sort_order
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
        partner_ids = list({p['partner_id'] for p in partners})

        self.env.cr.execute(query_detail, [
            tuple(account_codes),   # IN %s  (previous_balance)
            date_start,             # < %s   (previous_balance)
            tuple(partner_ids),     # IN %s  (previous_balance)
            tuple(account_codes),   # IN %s  (period_lines)
            date_start,             # >= %s  (period_lines)
            date_end,               # <= %s  (period_lines)
            tuple(partner_ids),     # IN %s  (period_lines)
        ])

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
