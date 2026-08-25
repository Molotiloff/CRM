from __future__ import annotations

from datetime import date

from db_asyncpg.repositories.base import ConnectionBoundRepo


class CrmStatsRepo(ConnectionBoundRepo):
    """
    Агрегаты для статистик CRM — SQL-эквиваленты формул боевой Google-таблицы
    (реверс в workflow_crm.md, раздел 4). Источники: deals (через view
    crm_sales / crm_purchases), expenses, cash-chat ledger, client_accounts,
    internal_accounts, capital_*, firm_position_moves.
    """

    # -- Лист «Статистика»: объёмы/доход по валюте × городу ---------------------

    async def sales_stats(
        self, date_from: date | None = None, date_to: date | None = None
    ) -> list[dict]:
        """
        По каждой валюте: строка-итог (city IS NULL) + строки по городам.
        qty          — Σ «Кол-во»
        rub_volume   — Σ «Сумма продажи»
        income       — Σ «Прибыль»
        Средний спред (income/rub_volume) и доли считает сервис.
        """
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT currency_code,
                       CASE WHEN GROUPING(city) = 1 THEN NULL ELSE city END AS city,
                       COALESCE(SUM(qty), 0)          AS qty,
                       COALESCE(SUM(sale_amount), 0)  AS rub_volume,
                       COALESCE(SUM(profit), 0)       AS income
                FROM crm_sales
                WHERE ($1::date IS NULL OR deal_at >= $1)
                  AND ($2::date IS NULL OR deal_at <= $2)
                GROUP BY GROUPING SETS ((currency_code), (currency_code, city))
                ORDER BY currency_code, city NULLS FIRST
                """,
                date_from,
                date_to,
            )
            return [dict(r) for r in rows]

    # -- «Главная»: прибыль по городам (продажи + сделки) ------------------------

    async def profit_by_city(
        self, date_from: date | None = None, date_to: date | None = None
    ) -> list[dict]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT btrim(city) AS city,
                       COALESCE(SUM(profit) FILTER (WHERE deal_type = 'sale'), 0) AS sales_profit,
                       COALESCE(SUM(profit) FILTER (WHERE deal_type NOT IN ('sale','purchase')), 0)
                           AS deals_profit
                FROM deals
                WHERE status = 'done'
                  AND profit IS NOT NULL
                  AND ($1::date IS NULL OR deal_at >= $1)
                  AND ($2::date IS NULL OR deal_at <= $2)
                GROUP BY 1
                ORDER BY 1
                """,
                date_from,
                date_to,
            )
            return [dict(r) for r in rows]

    # -- Лист «Прибыль»: дневная P&L ---------------------------------------------

    async def daily_pnl(self, date_from: date, date_to: date) -> list[dict]:
        """
        За каждую дату: доход с продаж + доход со сделок − расходы за дату.
        «Доход со сделок» — profit всех типов, кроме sale/purchase (лист «Сделки»).
        """
        async with self._connection() as con:
            rows = await con.fetch(
                """
                WITH income AS (
                    SELECT deal_at AS day,
                           COALESCE(SUM(profit) FILTER (WHERE deal_type = 'sale'), 0) AS sales_income,
                           COALESCE(SUM(profit) FILTER (WHERE deal_type NOT IN ('sale','purchase')), 0)
                               AS deals_income
                    FROM deals
                    WHERE status = 'done' AND profit IS NOT NULL
                      AND deal_at BETWEEN $1 AND $2
                    GROUP BY 1
                ),
                spent AS (
                    SELECT expense_at AS day, COALESCE(SUM(amount), 0) AS expenses
                    FROM expenses
                    WHERE expense_at BETWEEN $1 AND $2
                    GROUP BY 1
                )
                SELECT COALESCE(i.day, s.day)          AS day,
                       COALESCE(i.sales_income, 0)     AS sales_income,
                       COALESCE(i.deals_income, 0)     AS deals_income,
                       COALESCE(s.expenses, 0)         AS expenses
                FROM income i
                FULL JOIN spent s USING (day)
                ORDER BY 1
                """,
                date_from,
                date_to,
            )
            return [dict(r) for r in rows]

    # -- Лист «стата»: месячная сводная --------------------------------------------

    async def monthly_summary(self, salary_categories: tuple[str, ...]) -> list[dict]:
        """
        По месяцам: Доход (Σ profit), Расход (Σ expenses), ЗП (расходы
        категорий salary_categories). Прибыль/«расходы без ЗП» считает сервис.
        """
        async with self._connection() as con:
            rows = await con.fetch(
                """
                WITH income AS (
                    SELECT date_trunc('month', deal_at)::date AS month,
                           COALESCE(SUM(profit), 0) AS income
                    FROM deals
                    WHERE status = 'done' AND profit IS NOT NULL
                    GROUP BY 1
                ),
                spent AS (
                    SELECT date_trunc('month', expense_at)::date AS month,
                           COALESCE(SUM(amount), 0) AS expense,
                           COALESCE(SUM(amount) FILTER (WHERE category = ANY($1)), 0) AS salary
                    FROM expenses
                    GROUP BY 1
                )
                SELECT COALESCE(i.month, s.month) AS month,
                       COALESCE(i.income, 0)      AS income,
                       COALESCE(s.expense, 0)     AS expense,
                       COALESCE(s.salary, 0)      AS salary
                FROM income i
                FULL JOIN spent s USING (month)
                ORDER BY 1
                """,
                list(salary_categories),
            )
            return [dict(r) for r in rows]

    # -- Лист «Контрагенты»: объём/прибыль по клиентам ------------------------------

    async def counterparty_report(
        self, date_from: date | None = None, date_to: date | None = None
    ) -> list[dict]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT s.client_id,
                       COALESCE(c.name, '—') AS client_name,
                       COALESCE(SUM(s.sale_amount), 0) AS sale_volume_rub,
                       COALESCE(SUM(s.profit), 0)      AS profit,
                       COALESCE(SUM(s.qty), 0)         AS qty
                FROM crm_sales s
                LEFT JOIN clients c ON c.id = s.client_id
                WHERE ($1::date IS NULL OR s.deal_at >= $1)
                  AND ($2::date IS NULL OR s.deal_at <= $2)
                GROUP BY 1, 2
                ORDER BY 3 DESC
                """,
                date_from,
                date_to,
            )
            return [dict(r) for r in rows]

    # -- Лист «Оборотка»: вклады владельцев, доли, «платим в месяц» -----------------

    async def turnover_by_owner(self) -> list[dict]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT o.id AS owner_id,
                       o.name,
                       o.monthly_rate,
                       COALESCE(SUM(m.amount), 0) AS invested
                FROM capital_owners o
                LEFT JOIN capital_moves m ON m.owner_id = o.id
                WHERE o.is_active
                GROUP BY 1, 2, 3
                ORDER BY 4 DESC
                """
            )
            return [dict(r) for r in rows]

    # -- «Главная»: компоненты балансовой сверки (спека 4.3) -------------------------

    async def reconciliation_components(self) -> dict:
        """
        Скалярные компоненты сверки. Конвенция знаков — как в client_accounts:
        положительный баланс = средства клиента/счёта у фирмы (обязательство),
        отрицательный = должен фирме (дебиторка).
        """
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT
                    (SELECT COALESCE(SUM(account.balance), 0)
                       FROM cash_chat_registry cash
                       JOIN client_accounts account ON account.client_id = cash.client_id
                      WHERE cash.is_active AND account.is_active
                        AND UPPER(BTRIM(account.currency_code)) = 'RUB')    AS rub_cash,
                    (SELECT COALESCE(SUM(p.rub_cost_after), 0) FROM (
                         SELECT DISTINCT ON (currency_code) rub_cost_after
                         FROM firm_position_moves
                         ORDER BY currency_code, id DESC
                     ) p)                                                   AS rub_in_currency,
                    (SELECT COALESCE(SUM(balance), 0) FROM client_accounts
                      WHERE is_active AND upper(currency_code) = 'RUB'
                        AND NOT EXISTS (
                            SELECT 1 FROM cash_chat_registry cash
                            WHERE cash.client_id = client_accounts.client_id
                              AND cash.is_active
                        ))                                                  AS client_rub,
                    (SELECT COALESCE(SUM(balance), 0) FROM internal_accounts
                      WHERE is_active)                                      AS internal_rub,
                    (SELECT COALESCE(SUM(amount), 0) FROM capital_moves)    AS capital_invested,
                    (SELECT COALESCE(SUM(profit), 0) FROM deals
                      WHERE status = 'done')                                AS deals_profit_total,
                    (SELECT COALESCE(SUM(amount), 0) FROM expenses)         AS expenses_total
                """
            )
            return dict(row)

    async def client_balances_by_currency(self) -> dict[str, object]:
        """Σ клиентских балансов по каждой валюте (для «Факт валюты», 4.3)."""
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT upper(currency_code) AS currency_code,
                       COALESCE(SUM(balance), 0) AS total
                FROM client_accounts
                WHERE is_active
                  AND NOT EXISTS (
                      SELECT 1 FROM cash_chat_registry cash
                      WHERE cash.client_id = client_accounts.client_id
                        AND cash.is_active
                  )
                GROUP BY 1
                """
            )
            return {r["currency_code"]: r["total"] for r in rows}

    async def cash_ledger_balances(self) -> list[dict]:
        """Physical cash balances backed by registered cash-chat ledgers."""
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT cash.city,
                       cash.location_name AS name,
                       UPPER(BTRIM(account.currency_code)) AS currency_code,
                       account.balance
                FROM cash_chat_registry cash
                JOIN client_accounts account ON account.client_id = cash.client_id
                WHERE cash.is_active AND account.is_active
                ORDER BY cash.city, cash.location_name, currency_code
                """
            )
            return [dict(r) for r in rows]
