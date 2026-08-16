from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from db_asyncpg.repositories.crm_stats import CrmStatsRepo
from db_asyncpg.repositories.firm_positions import FirmPositionsRepo

# Категории расходов, считающиеся зарплатой (колонка «ЗП» листа «стата»
# в таблице вбивается руками; здесь — по категориям справочника).
SALARY_CATEGORIES: tuple[str, ...] = ("ЗП", "Переработка")


def _dec(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


# -- Лист «Статистика»: продажи по валюте × городу --------------------------------

@dataclass(slots=True, frozen=True)
class SalesStatRow:
    currency_code: str
    city: str | None            # None = «Общее»
    qty: Decimal                # объём валюты
    rub_volume: Decimal         # Σ «Сумма продажи»
    income: Decimal             # Σ «Прибыль»
    avg_spread: Decimal         # «Средний спред» = income / rub_volume
    share: Decimal              # «Доля от общего» = qty / qty(Общее)


@dataclass(slots=True, frozen=True)
class SalesCurrencyStat:
    currency_code: str
    total: SalesStatRow
    by_city: list[SalesStatRow]
    unassigned_qty: Decimal     # «Не занесено» = Общее − Σ городов


# -- «Главная»: прибыль по городам --------------------------------------------------

@dataclass(slots=True, frozen=True)
class CityProfit:
    city: str
    sales_profit: Decimal       # «Доход Екб» = Σ прибыли продаж города
    deals_profit: Decimal       # прибыль «Сделок» города
    total: Decimal


# -- Лист «Прибыль»: дневная P&L ----------------------------------------------------

@dataclass(slots=True, frozen=True)
class DailyPnl:
    day: date
    sales_income: Decimal       # «Доход с продажи»
    deals_income: Decimal       # «Доход со сделок»
    total_income: Decimal       # «Общий Доход»
    expenses: Decimal           # «Сумма Расход»
    profit: Decimal             # «Прибыль»


# -- Лист «стата»: месячная сводная --------------------------------------------------

@dataclass(slots=True, frozen=True)
class MonthlySummary:
    month: date                 # 1-е число месяца
    income: Decimal             # «Доход»
    expense: Decimal            # «Расход»
    profit: Decimal             # «Прибыль» = доход − расход
    salary: Decimal             # «ЗП»
    expense_wo_salary: Decimal  # «Расходы» = расход − ЗП


# -- Лист «Контрагенты»: объёмы по клиентам -------------------------------------------

@dataclass(slots=True, frozen=True)
class ClientVolumeRow:
    client_id: int | None
    client_name: str
    sale_volume_rub: Decimal    # «Продажа»
    profit: Decimal             # «Прибыль»
    qty: Decimal                # «Объем» (валюта)


# -- Лист «Оборотка»: доли владельцев --------------------------------------------------

@dataclass(slots=True, frozen=True)
class OwnerShare:
    owner_id: int
    name: str
    invested: Decimal           # Σ вкладов − выводов
    share: Decimal              # «% от общей»
    monthly_rate: Decimal | None    # «Залог под %»
    monthly_payment: Decimal    # «Платим в мес.» = вклад × ставка


@dataclass(slots=True, frozen=True)
class TurnoverSummary:
    owners: list[OwnerShare]
    total_invested: Decimal     # «Общая»
    total_monthly_payment: Decimal


# -- «Главная»: сверка (спека 4.3) ------------------------------------------------------

@dataclass(slots=True, frozen=True)
class CurrencyFact:
    currency_code: str
    position_qty: Decimal       # «кол-во» (позиция фирмы)
    rub_cost: Decimal           # «в рубле»
    avg_rate: Decimal           # «курс»
    client_qty: Decimal         # «Клиент.» = Σ клиентских балансов в валюте
    fact_qty: Decimal           # «Факт» = позиция + клиентские
    wallet_fact: Decimal | None # фактический остаток кошелька (руками/Tronscan)
    gap: Decimal | None         # «Разрыв» = wallet_fact − fact_qty


@dataclass(slots=True, frozen=True)
class Reconciliation:
    rub_cash: Decimal           # Σ рублёвых касс («RUB ЕКБ» + …)
    rub_in_currency: Decimal    # «RUB в Валюте» = Σ rub_cost позиций
    total_rub: Decimal          # «Общий RUB»
    client_rub: Decimal         # «Балансы клиентов» (RUB, знаковые)
    internal_rub: Decimal       # «Балансы SkyEx»
    total_balances: Decimal     # «Общий Балансы»
    fact_turnover: Decimal      # «Факт. Оборот» = Общий RUB − Общий Балансы
    capital_invested: Decimal   # «Оборотка» = Σ вкладов владельцев
    accumulated_profit: Decimal # «Общая Прибыль» = Σ profit − Σ расходов
    turnover: Decimal           # «Оборот» = Оборотка + Общая Прибыль
    gap: Decimal                # «Разрыв» = Факт. Оборот − Оборот (≈ 0)
    fact_rub: Decimal           # «Факт. RUB» = Общий RUB + Общий Балансы
    currencies: list[CurrencyFact] = field(default_factory=list)


class StatisticsService:
    """
    Расчёт значений статистик CRM. Каждый метод — эквивалент листа/блока
    боевой Google-таблицы; CRM должна давать те же цифры (workflow_crm.md, 4).
    """

    def __init__(
        self,
        repo: CrmStatsRepo,
        positions_repo: FirmPositionsRepo,
        salary_categories: tuple[str, ...] = SALARY_CATEGORIES,
    ) -> None:
        self.repo = repo
        self.positions_repo = positions_repo
        self.salary_categories = salary_categories

    # -- «Статистика» ---------------------------------------------------------------

    async def sales_statistics(
        self, date_from: date | None = None, date_to: date | None = None
    ) -> list[SalesCurrencyStat]:
        rows = await self.repo.sales_stats(date_from, date_to)
        totals: dict[str, dict] = {}
        cities: dict[str, list[dict]] = {}
        for r in rows:
            cur = str(r["currency_code"])
            if r["city"] is None:
                totals[cur] = r
            else:
                cities.setdefault(cur, []).append(r)

        result: list[SalesCurrencyStat] = []
        for cur, t in totals.items():
            total_qty = _dec(t["qty"])

            def make_row(
                r: dict, city: str | None, cur: str = cur, total_qty: Decimal = total_qty
            ) -> SalesStatRow:
                qty, rub, inc = _dec(r["qty"]), _dec(r["rub_volume"]), _dec(r["income"])
                return SalesStatRow(
                    currency_code=cur,
                    city=city,
                    qty=qty,
                    rub_volume=rub,
                    income=inc,
                    avg_spread=inc / rub if rub else Decimal(0),
                    share=qty / total_qty if total_qty else Decimal(0),
                )

            by_city = [make_row(r, str(r["city"])) for r in cities.get(cur, [])]
            result.append(
                SalesCurrencyStat(
                    currency_code=cur,
                    total=make_row(t, None),
                    by_city=by_city,
                    unassigned_qty=total_qty - sum((r.qty for r in by_city), Decimal(0)),
                )
            )
        return result

    # -- Прибыль по городам ------------------------------------------------------------

    async def profit_by_city(
        self, date_from: date | None = None, date_to: date | None = None
    ) -> list[CityProfit]:
        rows = await self.repo.profit_by_city(date_from, date_to)
        return [
            CityProfit(
                city=str(r["city"]),
                sales_profit=_dec(r["sales_profit"]),
                deals_profit=_dec(r["deals_profit"]),
                total=_dec(r["sales_profit"]) + _dec(r["deals_profit"]),
            )
            for r in rows
        ]

    # -- «Прибыль»: дневная P&L ----------------------------------------------------------

    async def daily_pnl(self, date_from: date, date_to: date) -> list[DailyPnl]:
        rows = await self.repo.daily_pnl(date_from, date_to)
        result = []
        for r in rows:
            sales, deals, exp = _dec(r["sales_income"]), _dec(r["deals_income"]), _dec(r["expenses"])
            result.append(
                DailyPnl(
                    day=r["day"],
                    sales_income=sales,
                    deals_income=deals,
                    total_income=sales + deals,
                    expenses=exp,
                    profit=sales + deals - exp,
                )
            )
        return result

    # -- «стата»: месячная сводная ---------------------------------------------------------

    async def monthly_summary(self) -> list[MonthlySummary]:
        rows = await self.repo.monthly_summary(self.salary_categories)
        return [
            MonthlySummary(
                month=r["month"],
                income=_dec(r["income"]),
                expense=_dec(r["expense"]),
                profit=_dec(r["income"]) - _dec(r["expense"]),
                salary=_dec(r["salary"]),
                expense_wo_salary=_dec(r["expense"]) - _dec(r["salary"]),
            )
            for r in rows
        ]

    # -- «Контрагенты»: объёмы по клиентам ---------------------------------------------------

    async def client_volumes(
        self, date_from: date | None = None, date_to: date | None = None
    ) -> list[ClientVolumeRow]:
        rows = await self.repo.counterparty_report(date_from, date_to)
        return [
            ClientVolumeRow(
                client_id=r["client_id"],
                client_name=str(r["client_name"]),
                sale_volume_rub=_dec(r["sale_volume_rub"]),
                profit=_dec(r["profit"]),
                qty=_dec(r["qty"]),
            )
            for r in rows
        ]

    # -- «Оборотка» ----------------------------------------------------------------------------

    async def turnover_summary(self) -> TurnoverSummary:
        rows = await self.repo.turnover_by_owner()
        total = sum((_dec(r["invested"]) for r in rows), Decimal(0))
        owners = []
        for r in rows:
            invested = _dec(r["invested"])
            rate = _dec(r["monthly_rate"]) if r["monthly_rate"] is not None else None
            owners.append(
                OwnerShare(
                    owner_id=int(r["owner_id"]),
                    name=str(r["name"]),
                    invested=invested,
                    share=invested / total if total else Decimal(0),
                    monthly_rate=rate,
                    monthly_payment=invested * rate if rate is not None else Decimal(0),
                )
            )
        return TurnoverSummary(
            owners=owners,
            total_invested=total,
            total_monthly_payment=sum((o.monthly_payment for o in owners), Decimal(0)),
        )

    # -- «Главная»: балансовая сверка (4.3) -------------------------------------------------------

    async def reconciliation(self) -> Reconciliation:
        c = await self.repo.reconciliation_components()
        rub_cash = _dec(c["rub_cash"])
        rub_in_currency = _dec(c["rub_in_currency"])
        total_rub = rub_cash + rub_in_currency
        client_rub = _dec(c["client_rub"])
        internal_rub = _dec(c["internal_rub"])
        total_balances = client_rub + internal_rub
        fact_turnover = total_rub - total_balances
        capital = _dec(c["capital_invested"])
        accumulated_profit = _dec(c["deals_profit_total"]) - _dec(c["expenses_total"])
        turnover = capital + accumulated_profit

        positions = await self.positions_repo.list_positions()
        client_by_cur = await self.repo.client_balances_by_currency()
        wallet_facts = await self.positions_repo.get_wallet_facts()
        currencies = []
        for p in positions:
            cur = str(p["currency_code"])
            if cur == "RUB":
                continue
            qty = _dec(p["qty_after"])
            rub_cost = _dec(p["rub_cost_after"])
            client_qty = _dec(client_by_cur.get(cur, 0))
            fact_qty = qty + client_qty
            wallet_fact = wallet_facts.get(cur)
            currencies.append(
                CurrencyFact(
                    currency_code=cur,
                    position_qty=qty,
                    rub_cost=rub_cost,
                    avg_rate=rub_cost / qty if qty else Decimal(0),
                    client_qty=client_qty,
                    fact_qty=fact_qty,
                    wallet_fact=wallet_fact,
                    gap=(wallet_fact - fact_qty) if wallet_fact is not None else None,
                )
            )

        return Reconciliation(
            rub_cash=rub_cash,
            rub_in_currency=rub_in_currency,
            total_rub=total_rub,
            client_rub=client_rub,
            internal_rub=internal_rub,
            total_balances=total_balances,
            fact_turnover=fact_turnover,
            capital_invested=capital,
            accumulated_profit=accumulated_profit,
            turnover=turnover,
            gap=fact_turnover - turnover,
            fact_rub=total_rub + total_balances,
            currencies=currencies,
        )
