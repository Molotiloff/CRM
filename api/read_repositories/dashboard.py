from __future__ import annotations

from datetime import date
from decimal import Decimal

from db_asyncpg.repositories.base import ConnectionBoundRepo
from domain import calculate_reconciliation
from services.accounting.models import (
    MainDashboardCity,
    MainDashboardCurrency,
    MainDashboardOperations,
    MainDashboardPeriods,
    MainDashboardReconciliation,
    MainDashboardSnapshot,
)

_CURRENCIES = ("EUR", "USDT", "USD_BL", "USD_WH")


def _decimal(value: object | None) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


class DashboardReadRepository(ConnectionBoundRepo):
    async def get_snapshot(self, *, business_date: date) -> MainDashboardSnapshot:
        async with self._connection() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                snapshot_meta = await connection.fetchrow(
                    """
                    SELECT transaction_timestamp() AS data_as_of,
                           (SELECT COUNT(*) FROM client_accounts) AS account_count
                    """
                )
                data_as_of = snapshot_meta["data_as_of"]
                await self._after_snapshot_started()
                positions = await connection.fetch(
                    """
                    SELECT DISTINCT ON (currency_code)
                           currency_code, qty_after, rub_cost_after
                    FROM firm_position_moves
                    WHERE currency_code = ANY($1::text[])
                    ORDER BY currency_code, id DESC
                    """,
                    list(_CURRENCIES),
                )
                client_balances = await connection.fetch(
                    """
                    SELECT CASE UPPER(BTRIM(account.currency_code))
                               WHEN 'USD' THEN 'USD_BL'
                               WHEN 'USDB' THEN 'USD_BL'
                               WHEN 'USDW' THEN 'USD_WH'
                               ELSE UPPER(BTRIM(account.currency_code))
                           END AS currency_code,
                           COALESCE(SUM(account.balance), 0) AS balance
                    FROM client_accounts account
                    JOIN clients client ON client.id = account.client_id
                    WHERE account.is_active
                      AND COALESCE(client.client_group, '') <> 'internal_wallet'
                      AND NOT EXISTS (
                          SELECT 1 FROM cash_chat_registry cash
                          WHERE cash.client_id = account.client_id
                            AND cash.is_active
                            AND (
                                cash.cash_currency_codes IS NULL
                                OR UPPER(BTRIM(account.currency_code))
                                   = ANY(cash.cash_currency_codes)
                            )
                      )
                    GROUP BY 1
                    """
                )
                internal_currency_balances = await connection.fetch(
                    """
                    SELECT currency_code, COALESCE(SUM(balance), 0) AS balance
                    FROM internal_accounts
                    WHERE is_active AND currency_code <> 'RUB'
                    GROUP BY currency_code
                    """
                )
                cash_balances = await connection.fetch(
                    """
                    SELECT CASE UPPER(BTRIM(account.currency_code))
                               WHEN 'USD' THEN 'USD_BL'
                               WHEN 'USDB' THEN 'USD_BL'
                               WHEN 'USDW' THEN 'USD_WH'
                               ELSE UPPER(BTRIM(account.currency_code))
                           END AS currency_code,
                           COALESCE(SUM(account.balance), 0) AS balance
                    FROM cash_chat_registry cash
                    JOIN client_accounts account ON account.client_id = cash.client_id
                    WHERE cash.is_active AND account.is_active
                      AND (
                          cash.cash_currency_codes IS NULL
                          OR UPPER(BTRIM(account.currency_code))
                             = ANY(cash.cash_currency_codes)
                      )
                    GROUP BY 1
                    """
                )
                wallet_facts = await connection.fetch(
                    """
                    SELECT DISTINCT ON (currency_code)
                           currency_code, actual_qty, observed_at
                    FROM firm_wallet_fact_snapshots
                    ORDER BY currency_code, observed_at DESC, id DESC
                    """
                )
                totals = await connection.fetchrow(
                    """
                    SELECT
                        (SELECT COALESCE(SUM(balance), 0) FROM internal_accounts
                          WHERE is_active AND currency_code = 'RUB') AS skyex_balances,
                        (SELECT COALESCE(SUM(balance), 0) FROM cash_desks
                          WHERE is_active AND currency_code = 'RUB'
                            AND city = 'мск' AND name IN ('Поэты', 'BS')
                            AND NOT EXISTS (
                                SELECT 1 FROM cash_chat_registry cash
                                WHERE cash.is_active
                                  AND LOWER(BTRIM(cash.city)) = 'мск'
                                  AND LOWER(BTRIM(cash.location_name))
                                      = LOWER(BTRIM(cash_desks.name))
                                  AND (
                                      cash.cash_currency_codes IS NULL
                                      OR 'RUB' = ANY(cash.cash_currency_codes)
                                  )
                            ))
                            AS manual_rub_cash,
                        (SELECT COALESCE(SUM(amount), 0) FROM capital_moves)
                            AS invested_capital,
                        (SELECT COALESCE(SUM(profit), 0) FROM deals
                          WHERE status = 'done') AS income,
                        (SELECT COALESCE(SUM(amount), 0) FROM expenses) AS expenses,
                        (SELECT COALESCE(SUM(profit), 0) FROM deals
                          WHERE status = 'done' AND deal_at = $1) AS daily_income,
                        (SELECT COALESCE(SUM(amount), 0) FROM expenses
                          WHERE expense_at = $1) AS daily_expense,
                        (SELECT COALESCE(SUM(
                            COALESCE(NULLIF(body->>'sale_amount', '')::numeric, 0)
                         ), 0) FROM deals
                          WHERE status = 'done' AND deal_type = 'sale' AND deal_at = $1)
                            AS daily_turnover,
                        (SELECT COALESCE(SUM(
                            COALESCE(NULLIF(body->>'rub_cost', '')::numeric, 0)
                         ), 0) FROM deals
                          WHERE status = 'done' AND deal_type = 'sale'
                            AND deal_at >= date_trunc('month', $1::date)::date
                            AND deal_at < (date_trunc('month', $1::date)
                                + INTERVAL '1 month')::date) AS monthly_turnover,
                        (SELECT COUNT(DISTINCT account.client_id)
                           FROM client_accounts account
                           JOIN clients client ON client.id = account.client_id
                          WHERE account.is_active AND account.balance <> 0
                            AND COALESCE(client.client_group, '') <> 'internal_wallet'
                            AND NOT EXISTS (
                                SELECT 1 FROM cash_chat_registry cash
                                WHERE cash.client_id = account.client_id
                                  AND cash.is_active
                                  AND (
                                      cash.cash_currency_codes IS NULL
                                      OR UPPER(BTRIM(account.currency_code))
                                         = ANY(cash.cash_currency_codes)
                                  )
                            )) AS clients_with_balance,
                        (SELECT COUNT(*) FROM exchange_request_links
                          WHERE status = 'active') AS active_exchange_requests,
                        (SELECT COUNT(*) FROM request_schedule_entries
                          WHERE is_active) AS active_schedule_requests,
                        (SELECT COALESCE(SUM(qty), 0) FROM usdt_fulfillment_queue
                          WHERE status IN ('queued', 'executing')) AS queued_usdt_qty,
                        (SELECT COALESCE(SUM(qty), 0) FROM profit_usdt_accruals accrual
                          JOIN deals deal ON deal.id = accrual.deal_id
                          WHERE accrual.capitalization_status = 'pending'
                            AND deal.deal_at = $1) AS deal_profit_qty,
                        (SELECT COALESCE(SUM(qty), 0) FROM profit_usdt_accruals
                          WHERE settlement_status = 'in_transit') AS profit_in_transit_qty
                    """,
                    business_date,
                )
                city_rows = await connection.fetch(
                    """
                    WITH components AS (
                        SELECT LOWER(BTRIM(city)) AS city, profit AS income,
                               0::numeric AS expense, 0::bigint AS active_requests
                        FROM deals
                        WHERE status = 'done'
                        UNION ALL
                        SELECT LOWER(BTRIM(city)), 0, amount, 0
                        FROM expenses
                        WHERE city IS NOT NULL
                        UNION ALL
                        SELECT LOWER(BTRIM(city)), 0, 0, 1
                        FROM request_schedule_entries
                        WHERE is_active
                    ), normalized AS (
                        SELECT CASE city
                                   WHEN 'тюмень' THEN 'тюм'
                                   WHEN 'москва' THEN 'мск'
                                   WHEN 'новосибирск' THEN 'нск'
                                   WHEN 'санкт-петербург' THEN 'спб'
                                   ELSE city
                               END AS city,
                               income, expense, active_requests
                        FROM components
                    )
                    SELECT city, COALESCE(SUM(income), 0) AS income,
                           COALESCE(SUM(expense), 0) AS expense,
                           COALESCE(SUM(active_requests), 0) AS active_requests
                    FROM normalized
                    GROUP BY city
                    ORDER BY 1
                    """
                )

        position_by_code = {str(row["currency_code"]): row for row in positions}
        client_by_code = {
            str(row["currency_code"]): _decimal(row["balance"]) for row in client_balances
        }
        for row in internal_currency_balances:
            code = str(row["currency_code"])
            client_by_code[code] = client_by_code.get(code, Decimal(0)) + _decimal(
                row["balance"]
            )
        cash_by_code = {
            str(row["currency_code"]): _decimal(row["balance"]) for row in cash_balances
        }
        fact_by_code = {str(row["currency_code"]): row for row in wallet_facts}
        deal_profit_qty = _decimal(totals["deal_profit_qty"])

        currencies = []
        for code in _CURRENCIES:
            position = position_by_code.get(code)
            free_qty = _decimal(position["qty_after"] if position else None)
            rub_cost = _decimal(position["rub_cost_after"] if position else None)
            client_qty = client_by_code.get(code, Decimal(0))
            profit_qty = deal_profit_qty if code == "USDT" else Decimal(0)
            fact_qty = free_qty + client_qty + profit_qty
            observed = fact_by_code.get(code)
            observed_qty = (
                _decimal(observed["actual_qty"]) if observed is not None else cash_by_code.get(code)
            )
            currencies.append(
                MainDashboardCurrency(
                    code=code,
                    free_qty=free_qty,
                    internal_rate=rub_cost / free_qty if free_qty else Decimal(0),
                    rub_cost=rub_cost,
                    client_qty=client_qty,
                    deal_profit_qty=profit_qty,
                    fact_qty=fact_qty,
                    observed_qty=observed_qty,
                    gap=observed_qty - fact_qty if observed_qty is not None else None,
                    observed_at=observed["observed_at"] if observed is not None else None,
                )
            )

        rub_cash = cash_by_code.get("RUB", Decimal(0)) + _decimal(totals["manual_rub_cash"])
        rub_in_currency = sum((item.rub_cost for item in currencies), Decimal(0))
        client_rub = client_by_code.get("RUB", Decimal(0))
        skyex_balances = _decimal(totals["skyex_balances"])
        income = _decimal(totals["income"])
        expenses = _decimal(totals["expenses"])
        invested_capital = _decimal(totals["invested_capital"])
        reconciliation = calculate_reconciliation(
            rub_cash=rub_cash,
            rub_in_currency=rub_in_currency,
            client_balances=client_rub,
            skyex_balances=skyex_balances,
            invested_capital=invested_capital,
            income=income,
            expenses=expenses,
        )
        daily_income = _decimal(totals["daily_income"])
        daily_expense = _decimal(totals["daily_expense"])
        monthly_turnover = _decimal(totals["monthly_turnover"])
        active_requests = int(totals["active_exchange_requests"]) + int(
            totals["active_schedule_requests"]
        )
        usdt_fact = next(item.fact_qty for item in currencies if item.code == "USDT")
        queued_usdt = _decimal(totals["queued_usdt_qty"])
        usdt_observed = next(item.observed_qty for item in currencies if item.code == "USDT")
        warnings = []
        if any(code != "RUB" and amount != 0 for code, amount in client_by_code.items()):
            warnings.append("Client FX balances are excluded from RUB valuation (stored_rub_only)")
        if usdt_observed is None:
            warnings.append("USDT physical balance has not been observed")
        else:
            usdt_currency = next(item for item in currencies if item.code == "USDT")
            if (
                usdt_currency.observed_at is not None
                and usdt_currency.observed_at.date() < business_date
            ):
                warnings.append("USDT physical balance is stale")

        return MainDashboardSnapshot(
            source="postgres",
            calculated_at=data_as_of,
            data_as_of=data_as_of,
            warnings=tuple(warnings),
            currencies=tuple(currencies),
            reconciliation=MainDashboardReconciliation(
                rub_cash=rub_cash,
                rub_in_currency=rub_in_currency,
                total_rub=reconciliation.total_rub,
                client_balances=client_rub,
                skyex_balances=skyex_balances,
                total_balances=reconciliation.total_balances,
                fact_turnover=reconciliation.fact_turnover,
                accumulated_profit=reconciliation.accumulated_profit,
                invested_capital=invested_capital,
                turnover=reconciliation.turnover,
                gap=reconciliation.gap,
                fact_rub=reconciliation.fact_rub,
            ),
            periods=MainDashboardPeriods(
                daily_income=daily_income,
                daily_expense=daily_expense,
                daily_profit=daily_income - daily_expense,
                daily_turnover=_decimal(totals["daily_turnover"]),
                monthly_turnover=monthly_turnover,
                profitability=income / monthly_turnover if monthly_turnover else None,
            ),
            cities=tuple(
                MainDashboardCity(
                    city=str(row["city"]),
                    income=_decimal(row["income"]),
                    expense=_decimal(row["expense"]),
                    profit=_decimal(row["income"]) - _decimal(row["expense"]),
                    active_requests=int(row["active_requests"]),
                )
                for row in city_rows
            ),
            operations=MainDashboardOperations(
                active_requests=active_requests,
                clients_with_balance=int(totals["clients_with_balance"]),
                queued_usdt_qty=queued_usdt,
                queue_shortage_qty=max(queued_usdt - usdt_fact, Decimal(0)),
                onchain_liquid_qty=usdt_observed,
                profit_in_transit_qty=_decimal(totals["profit_in_transit_qty"]),
            ),
        )

    async def _after_snapshot_started(self) -> None:
        """Test seam for proving repeatable-read behavior during concurrent writes."""
