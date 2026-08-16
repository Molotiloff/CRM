"""
Верификация статистик CRM против формул Google-таблицы.

Воспроизводит на тестовой БД реальные строки листов «Продажа» (строки 2–5),
«Покупка» (6–7), «Сделки», «Расходы» и сверяет расчёты сервисов с числами,
которые даёт таблица.

ВНИМАНИЕ: скрипт TRUNCATE'ит deals/expenses/клиентов и т.д. — запускать
ТОЛЬКО на одноразовой тестовой БД. Подготовка:
    createdb crmskyex_mig_test
    DATABASE_URL=postgresql://localhost:5432/crmskyex_mig_test alembic upgrade head
Запуск:
    python scripts/verify_crm_stats.py postgresql://localhost:5432/crmskyex_mig_test
"""
import asyncio
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_asyncpg.pool import close_pool, create_pool  # noqa: E402
from db_asyncpg.repositories.crm_stats import CrmStatsRepo  # noqa: E402
from db_asyncpg.repositories.firm_positions import FirmPositionsRepo  # noqa: E402
from services.crm import FirmPositionService, StatisticsService  # noqa: E402

if len(sys.argv) != 2:
    sys.exit("usage: verify_crm_stats.py <DSN тестовой БД (данные будут стёрты!)>")
DSN = sys.argv[1]
if "/botdb" in DSN:
    sys.exit("Отказ: это похоже на рабочую БД (botdb), скрипт стирает данные.")

FAILED = []


def check(name, got, expected, tol=Decimal("0.01")):
    got, expected = Decimal(str(got)), Decimal(str(expected))
    ok = abs(got - expected) <= tol
    print(f"  {'OK ' if ok else 'FAIL'} {name}: got={got} expected={expected}")
    if not ok:
        FAILED.append(name)


async def insert_sale(con, d, currency, entry, qty, exit_rate, client_id, city, kt_id, kt_spread):
    buy = qty * entry
    sale = qty * exit_rate
    profit = sale - buy
    kt_amount = kt_spread * qty
    await con.execute(
        """
        INSERT INTO deals (deal_type, city, client_id, counterparty_id, status, source,
                           body, profit, deal_at)
        VALUES ('sale', $1, $2, $3, 'done', 'import',
                jsonb_build_object(
                    'currency', $4::text, 'qty', $5::numeric, 'entry_rate', $6::numeric,
                    'exit_rate', $7::numeric, 'buy_amount', $8::numeric,
                    'sale_amount', $9::numeric, 'spread', ($7::numeric - $6::numeric),
                    'kt_spread', $10::numeric, 'kt_amount', $11::numeric,
                    'our_profit', ($12::numeric - $11::numeric)),
                $12, $13)
        """,
        city, client_id, kt_id, currency, qty, entry, exit_rate, buy, sale,
        kt_spread, kt_amount, profit, d,
    )


async def main():
    pool = await create_pool(DSN)
    positions_repo = FirmPositionsRepo(pool)
    pos = FirmPositionService(positions_repo)
    stats = StatisticsService(CrmStatsRepo(pool), positions_repo)

    async with pool.acquire() as con:
        # чистим от прошлых прогонов
        await con.execute(
            "TRUNCATE deals, deal_legs, deal_status_events, expenses, capital_moves, "
            "capital_payouts, firm_position_moves, firm_wallet_facts, cash_desks, "
            "cash_desk_moves, internal_accounts, internal_account_moves, "
            "client_comments, client_kt_fees RESTART IDENTITY CASCADE"
        )
        await con.execute("DELETE FROM client_accounts; DELETE FROM clients;")
        cl = {}
        for name in ("от Саши", "TAJO", "Blato", "BestChange"):
            cl[name] = await con.fetchval(
                "INSERT INTO clients(chat_id, name) VALUES (hashtext($1), $1) RETURNING id", name
            )
        kt = await con.fetchval(
            "INSERT INTO counterparties(name) VALUES ('тест КТ') "
            "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"
        )

    print("== Позиция фирмы (спека 4.2): покупка → средний курс → продажа ==")
    # «Покупка» строки 6–7: 925 по 75.0 (Члб), 31500 по 75.25 (Мск)
    await pos.apply_purchase(currency_code="USDT", qty=Decimal("925"), rate=Decimal("75.0"))
    p = await pos.apply_purchase(currency_code="USDT", qty=Decimal("31500"), rate=Decimal("75.25"))
    check("qty после покупок", p.qty, Decimal("32425"))
    check("rub_cost после покупок", p.rub_cost, Decimal("925") * Decimal("75") + Decimal("31500") * Decimal("75.25"))
    expected_avg = (Decimal("925") * Decimal("75") + Decimal("31500") * Decimal("75.25")) / Decimal("32425")
    check("средний курс (Вход)", p.avg_rate, expected_avg, tol=Decimal("0.000001"))

    # продажа 2292.49 без ручного входа → вход = средний
    entry, p2 = await pos.apply_sale(currency_code="USDT", qty=Decimal("2292.49"))
    check("вход = средний курс", entry, expected_avg, tol=Decimal("0.000001"))
    check("qty после продажи", p2.qty, Decimal("32425") - Decimal("2292.49"))
    # средний курс не должен меняться при списании по среднему
    check("средний курс стабилен", p2.avg_rate, expected_avg, tol=Decimal("0.000001"))

    print("== Лист «Продажа», строки 2–5 → deals(sale) ==")
    d = date(2026, 6, 1)
    async with pool.acquire() as con:
        # A2: USDT вход 75.22 кол-во 2292.49 выход 75.8, от Саши, Члб, КТ-спред 0.1
        await insert_sale(con, d, "USDT", Decimal("75.22"), Decimal("2292.49"), Decimal("75.8"),
                          cl["от Саши"], "Члб", kt, Decimal("0.1"))
        # A3: вход 75.1130360418605 кол-во 1599 выход 75.9, TAJO, Члб
        await insert_sale(con, d, "USDT", Decimal("75.1130360418605"), Decimal("1599"), Decimal("75.9"),
                          cl["TAJO"], "Члб", None, Decimal("0"))
        # A4: вход 75.25 кол-во 31537 выход 75.8, Blato, Екб, КТ-спред 0.3
        await insert_sale(con, d, "USDT", Decimal("75.25"), Decimal("31537"), Decimal("75.8"),
                          cl["Blato"], "Екб ", kt, Decimal("0.3"))
        # A5: вход 75.3 кол-во 84500/75.9 выход 75.6, BestChange, Члб, КТ-спред 0.3
        qty5 = Decimal("84500") / Decimal("75.9")
        await insert_sale(con, d, "USDT", Decimal("75.3"), qty5, Decimal("75.6"),
                          cl["BestChange"], "Члб", kt, Decimal("0.3"))

        # Расходы за 02.06 (лист «Расходы»): постоянный Аренда 30000 Члб + переменный 2184 Екб 01.06
        await con.execute(
            "INSERT INTO expenses(kind, category, city, amount, expense_at) VALUES "
            "('fixed','Аренда','Члб',30000,'2026-06-02'),"
            "('fixed','ЗП','Екб',50000,'2026-06-02'),"
            "('variable','Пропуска в офис Никита','Екб',2184,'2026-06-01')"
        )
        # Сделка типа «Прибыль» за 01.06 (лист «Сделки» A2): 3177796.23*3%
        await con.execute(
            """INSERT INTO deals(deal_type, city, status, source, body, profit, deal_at)
               VALUES ('profit', 'Екб', 'done', 'import',
                       jsonb_build_object('amount_expr','3177796.23*3%'), 95333.8869, '2026-06-01')"""
        )

    # Ожидания из формул листа:
    j2 = Decimal("2292.49") * Decimal("75.8") - Decimal("2292.49") * Decimal("75.22")   # 1329.6442
    j3 = Decimal("1599") * Decimal("75.9") - Decimal("1599") * Decimal("75.1130360418605")
    j4 = Decimal("31537") * Decimal("75.8") - Decimal("31537") * Decimal("75.25")       # 17345.35
    j5 = qty5 * Decimal("75.6") - qty5 * Decimal("75.3")                                # 333.99...
    total_income = j2 + j3 + j4 + j5
    total_qty = Decimal("2292.49") + Decimal("1599") + Decimal("31537") + qty5
    total_rub = (Decimal("2292.49") * Decimal("75.8") + Decimal("1599") * Decimal("75.9")
                 + Decimal("31537") * Decimal("75.8") + qty5 * Decimal("75.6"))

    print("== Статистика продаж (лист «Статистика») ==")
    st = await stats.sales_statistics()
    usdt = next(s for s in st if s.currency_code == "USDT")
    check("Общее кол-во", usdt.total.qty, total_qty, tol=Decimal("0.0001"))
    check("Общее руб-объём (Σ сумм продажи)", usdt.total.rub_volume, total_rub)
    check("Общий доход", usdt.total.income, total_income)
    check("Средний спред", usdt.total.avg_spread, total_income / total_rub, tol=Decimal("0.0000001"))
    ekb = next(r for r in usdt.by_city if r.city == "Екб")
    chlb = next(r for r in usdt.by_city if r.city == "Члб")
    check("Екб кол-во", ekb.qty, Decimal("31537"))
    check("Екб доход", ekb.income, j4)
    check("Екб доля", ekb.share, Decimal("31537") / total_qty, tol=Decimal("0.0000001"))
    check("Члб доход", chlb.income, j2 + j3 + j5)
    check("Не занесено", usdt.unassigned_qty, Decimal("0"), tol=Decimal("0.0001"))

    print("== Прибыль по городам («Главная» / 'Продажа'!V17..) ==")
    cp = {c.city: c for c in await stats.profit_by_city()}
    check("Екб прибыль продаж", cp["Екб"].sales_profit, j4)
    check("Члб прибыль продаж", cp["Члб"].sales_profit, j2 + j3 + j5)
    check("Екб прибыль сделок", cp["Екб"].deals_profit, Decimal("95333.8869"))

    print("== Дневная P&L (лист «Прибыль») ==")
    pnl = await stats.daily_pnl(date(2026, 6, 1), date(2026, 6, 2))
    d1 = next(r for r in pnl if r.day == date(2026, 6, 1))
    d2 = next(r for r in pnl if r.day == date(2026, 6, 2))
    check("01.06 доход с продажи", d1.sales_income, total_income)
    check("01.06 доход со сделок", d1.deals_income, Decimal("95333.8869"))
    check("01.06 расход", d1.expenses, Decimal("2184"))
    check("01.06 прибыль", d1.profit, total_income + Decimal("95333.8869") - Decimal("2184"))
    check("02.06 расход", d2.expenses, Decimal("80000"))
    check("02.06 прибыль", d2.profit, Decimal("-80000"))

    print("== Месячная сводная (лист «стата») ==")
    ms = await stats.monthly_summary()
    june = next(r for r in ms if r.month == date(2026, 6, 1))
    check("Доход июня", june.income, total_income + Decimal("95333.8869"))
    check("Расход июня", june.expense, Decimal("82184"))
    check("Прибыль июня", june.profit, total_income + Decimal("95333.8869") - Decimal("82184"))
    check("ЗП июня", june.salary, Decimal("50000"))
    check("Расходы без ЗП", june.expense_wo_salary, Decimal("32184"))

    print("== Контрагенты: объёмы по клиентам ==")
    cv = {r.client_name: r for r in await stats.client_volumes()}
    check("Blato продажа", cv["Blato"].sale_volume_rub, Decimal("31537") * Decimal("75.8"))
    check("Blato прибыль", cv["Blato"].profit, j4)
    check("Blato объём", cv["Blato"].qty, Decimal("31537"))

    print("== Оборотка: доли и «платим в мес.» ==")
    async with pool.acquire() as con:
        await con.execute(
            """INSERT INTO capital_moves(owner_id, amount, move_at)
               SELECT id, 3000000, '2026-03-18' FROM capital_owners WHERE name='Бабушка'"""
        )
        await con.execute(
            """INSERT INTO capital_moves(owner_id, amount, move_at)
               SELECT id, 10000000, '2026-03-18' FROM capital_owners WHERE name='Костя'"""
        )
    ts = await stats.turnover_summary()
    check("Общая оборотка", ts.total_invested, Decimal("13000000"))
    grand = {o.name: o for o in ts.owners}
    check("Бабушка платим в мес (3M×0.02)", grand["Бабушка"].monthly_payment, Decimal("60000"))
    check("Костя платим в мес (10M×0.025)", grand["Костя"].monthly_payment, Decimal("250000"))
    check("Костя доля", grand["Костя"].share, Decimal("10000000") / Decimal("13000000"), tol=Decimal("0.0000001"))

    print("== Сверка («Главная», 4.3) ==")
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO cash_desks(city, name, currency_code, balance) VALUES "
            "('Екб','', 'RUB', 2206000), ('Мск','Поэты','RUB', 677570)"
        )
        # клиентский RUB-баланс: Blato должен фирме 500000 (дебиторка → −500000)
        await con.execute(
            "INSERT INTO client_accounts(client_id, currency_code, precision, balance) "
            "VALUES ($1, 'RUB', 2, -500000), ($1, 'USDT', 2, 18843.22)", cl["Blato"]
        )
        await con.execute(
            "INSERT INTO internal_accounts(name, kind, balance) VALUES ('Баланс Никита','employee',152012.17)"
        )
    await pos.repo.set_wallet_fact(currency_code="USDT", actual_qty=Decimal("14"))

    rec = await stats.reconciliation()
    check("RUB кассы", rec.rub_cash, Decimal("2883570"))
    # позиция USDT: 32425 − 2292.49 = 30132.51; rub_cost = cost_before − 2292.49×avg
    pos_now = await pos.position("USDT")
    check("RUB в валюте", rec.rub_in_currency, pos_now.rub_cost)
    check("Общий RUB", rec.total_rub, Decimal("2883570") + pos_now.rub_cost)
    check("Балансы клиентов RUB", rec.client_rub, Decimal("-500000"))
    check("Балансы SkyEx", rec.internal_rub, Decimal("152012.17"))
    check("Факт. Оборот = Общий RUB − Общий Балансы",
          rec.fact_turnover, rec.total_rub - Decimal("-500000") - Decimal("152012.17"))
    check("Оборот = Оборотка + Общая Прибыль",
          rec.turnover, Decimal("13000000") + rec.accumulated_profit)
    check("Общая Прибыль = Σ profit − Σ расходов",
          rec.accumulated_profit, total_income + Decimal("95333.8869") - Decimal("82184"))
    check("Разрыв", rec.gap, rec.fact_turnover - rec.turnover)
    usdt_fact = next(c for c in rec.currencies if c.currency_code == "USDT")
    check("USDT Клиент.", usdt_fact.client_qty, Decimal("18843.22"))
    check("USDT Факт = позиция + клиент.", usdt_fact.fact_qty, pos_now.qty + Decimal("18843.22"))
    check("USDT Разрыв = факт кошелька − Факт", usdt_fact.gap, Decimal("14") - usdt_fact.fact_qty)

    await close_pool(pool)
    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} проверок: {FAILED}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


asyncio.run(main())
