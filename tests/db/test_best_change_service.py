from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from db_asyncpg.repositories.deals import DealRepository
from db_asyncpg.uow import AsyncpgUnitOfWork
from domain import DomainStateError, DomainValidationError
from services.best_change import (
    BestChangeAccount,
    BestChangeOperation,
    BestChangePaymentKind,
    BestChangeService,
    CloseBestChangeMonth,
    CorrectBestChangeDeal,
    RecordBestChangeDeal,
    RecordBestChangePayment,
    ReverseBestChangeMonth,
    ReverseBestChangePayment,
)

_CHAT_ID = -100987654321
_MANAGER_TG_ID = 900017


@pytest.mark.asyncio
async def test_best_change_service_posts_deal_and_balances_idempotently(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users(tg_user_id, display_name, role)
            VALUES ($1, 'BestChange Manager', 'manager')
            ON CONFLICT (tg_user_id) DO UPDATE SET is_active = TRUE
            """,
            _MANAGER_TG_ID,
        )
    service = BestChangeService(
        lambda: AsyncpgUnitOfWork(pool),
        chat_id=_CHAT_ID,
    )
    command = _command(
        message_id=77,
        operation=BestChangeOperation.SALE,
        qty="1000",
        market_rate="87",
        client_rate="88",
    )

    first = await service.record(command)
    repeated = await service.record(command)

    assert repeated.repeated is True
    assert repeated.deal_id == first.deal_id
    assert first.profit_balance_rub == Decimal("700.00")
    assert first.platform_fee_balance_usdt == Decimal("3.409091")
    async with pool.acquire() as connection:
        deal = await connection.fetchrow(
            """
            SELECT deal_type, city, status, source, source_kind, source_ref,
                   profit, deal_at, body
            FROM deals WHERE id = $1
            """,
            first.deal_id,
        )
        moves = await connection.fetch(
            """
            SELECT account_code, currency_code, amount, balance_after
            FROM best_change_account_moves ORDER BY id
            """
        )
        status_count = await connection.fetchval(
            "SELECT COUNT(*) FROM deal_status_events WHERE deal_id = $1",
            first.deal_id,
        )

    assert deal["deal_type"] == "best_change"
    assert deal["city"] == "члб"
    assert deal["status"] == "done"
    assert deal["source"] == "tg_bot"
    assert deal["source_kind"] == "best_change"
    assert deal["source_ref"] == f"{_CHAT_ID}:77"
    assert deal["profit"] == Decimal("350.00000000")
    assert deal["deal_at"] == date(2026, 8, 31)
    body = json.loads(deal["body"])
    assert body["operation_kind"] == "sale"
    assert body["skyex_profit_rub"] == "350.00"
    assert [tuple(move.values()) for move in moves] == [
        ("sale_chlb", "RUB", Decimal("700.00000000"), Decimal("700.00000000")),
        (
            "platform_fee",
            "USDT",
            Decimal("3.40909100"),
            Decimal("3.40909100"),
        ),
    ]
    assert status_count == 1
    assert await service.balances() == {
        BestChangeAccount.PURCHASE_TYM: Decimal("0"),
        BestChangeAccount.SALE_TYM: Decimal("0"),
        BestChangeAccount.PURCHASE_CHLB: Decimal("0"),
        BestChangeAccount.SALE_CHLB: Decimal("700.00000000"),
        BestChangeAccount.PLATFORM_FEE: Decimal("3.40909100"),
    }


@pytest.mark.asyncio
async def test_best_change_loss_posts_full_loss_without_coindrop_fee(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users(tg_user_id, display_name, role)
            VALUES ($1, 'BestChange Manager', 'manager')
            ON CONFLICT (tg_user_id) DO UPDATE SET is_active = TRUE
            """,
            _MANAGER_TG_ID,
        )
    service = BestChangeService(
        lambda: AsyncpgUnitOfWork(pool),
        chat_id=_CHAT_ID,
    )

    result = await service.record(
        _command(
            message_id=78,
            operation=BestChangeOperation.SALE,
            qty="100",
            market_rate="88",
            client_rate="87",
        )
    )

    assert result.calculation.profit_pool_rub == Decimal("-100.00")
    assert result.calculation.skyex_profit_rub == Decimal("-50.00")
    assert result.calculation.platform_fee_usdt == Decimal("0.000000")
    assert result.profit_balance_rub == Decimal("-100.00")
    assert result.platform_fee_balance_usdt == 0
    async with pool.acquire() as connection:
        moves = await connection.fetch(
            "SELECT account_code, amount FROM best_change_account_moves ORDER BY id"
        )
    assert [tuple(move.values()) for move in moves] == [
        ("sale_chlb", Decimal("-100.00000000")),
    ]


@pytest.mark.asyncio
async def test_best_change_correction_reverses_original_and_posts_replacement(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users(tg_user_id, display_name, role)
            VALUES ($1, 'BestChange Manager', 'manager')
            ON CONFLICT (tg_user_id) DO UPDATE SET is_active = TRUE
            """,
            _MANAGER_TG_ID,
        )
    service = BestChangeService(
        lambda: AsyncpgUnitOfWork(pool),
        chat_id=_CHAT_ID,
        today_factory=lambda: date(2026, 9, 1),
    )
    with pytest.raises(DomainValidationError, match="cannot be in the future"):
        await service.record(
            _command(
                message_id=76,
                operation=BestChangeOperation.SALE,
                qty="100",
                market_rate="87",
                client_rate="88",
                deal_at=date(2026, 9, 2),
            )
        )
    original = await service.record(
        _command(
            message_id=91,
            operation=BestChangeOperation.SALE,
            qty="1000",
            market_rate="87",
            client_rate="88",
        )
    )
    correction = CorrectBestChangeDeal(
        deal_id=original.deal_id,
        operation=BestChangeOperation.SALE,
        city="члб",
        qty_usdt=Decimal("1000"),
        market_rate_rub=Decimal("87"),
        client_rate_rub=Decimal("87.5"),
        actor_tg_user_id=_MANAGER_TG_ID,
        chat_id=_CHAT_ID,
        message_id=92,
        reason="Исправлен клиентский курс",
    )

    first = await service.correct_deal(correction)
    repeated = await service.correct_deal(correction)

    assert first.repeated is False
    assert repeated.repeated is True
    assert repeated.replacement.deal_id == first.replacement.deal_id
    assert first.replacement.calculation.profit_pool_rub == Decimal("350.00")
    assert first.replacement.calculation.platform_fee_usdt == Decimal("1.714286")
    balances = await service.balances()
    assert balances[BestChangeAccount.SALE_CHLB] == Decimal("350.00000000")
    assert balances[BestChangeAccount.PLATFORM_FEE] == Decimal("1.71428600")
    async with pool.acquire() as connection:
        deals = await connection.fetch(
            """
            SELECT id, status, profit, deal_at
            FROM deals WHERE id = ANY($1::bigint[]) ORDER BY id
            """,
            [original.deal_id, first.replacement.deal_id],
        )
        moves = await connection.fetch(
            """
            SELECT account_code, amount, deal_id, reversal_of_id
            FROM best_change_account_moves ORDER BY id
            """
        )
        correction_row = await connection.fetchrow(
            """
            SELECT original_deal_id, replacement_deal_id, reason
            FROM best_change_deal_corrections
            """
        )
    assert [(row["status"], row["profit"], row["deal_at"]) for row in deals] == [
        ("canceled", Decimal("350.00000000"), date(2026, 8, 31)),
        ("done", Decimal("175.00000000"), date(2026, 8, 31)),
    ]
    assert [(row["account_code"], row["amount"]) for row in moves] == [
        ("sale_chlb", Decimal("700.00000000")),
        ("platform_fee", Decimal("3.40909100")),
        ("sale_chlb", Decimal("-700.00000000")),
        ("platform_fee", Decimal("-3.40909100")),
        ("sale_chlb", Decimal("350.00000000")),
        ("platform_fee", Decimal("1.71428600")),
    ]
    assert [row["reversal_of_id"] for row in moves] == [None, None, 1, 2, None, None]
    assert tuple(correction_row.values()) == (
        original.deal_id,
        first.replacement.deal_id,
        "Исправлен клиентский курс",
    )
    deal_repository = DealRepository(pool)
    original_details = await deal_repository.get_deal(original.deal_id)
    replacement_details = await deal_repository.get_deal(first.replacement.deal_id)
    assert original_details is not None
    assert replacement_details is not None
    assert original_details.corrected_to_deal_id == first.replacement.deal_id
    assert replacement_details.corrected_from_deal_id == original.deal_id
    assert replacement_details.correction_reason == "Исправлен клиентский курс"
    await service.close_month(
        CloseBestChangeMonth(
            period_month=date(2026, 8, 1),
            actor_tg_user_id=_MANAGER_TG_ID,
            chat_id=_CHAT_ID,
            message_id=93,
        )
    )
    with pytest.raises(DomainStateError, match="before adding its deal"):
        await service.record(
            _command(
                message_id=95,
                operation=BestChangeOperation.SALE,
                qty="100",
                market_rate="87",
                client_rate="88",
                deal_at=date(2026, 8, 25),
            )
        )
    with pytest.raises(DomainStateError, match="Reverse the BestChange month closure"):
        await service.correct_deal(
            CorrectBestChangeDeal(
                deal_id=first.replacement.deal_id,
                operation=BestChangeOperation.SALE,
                city="члб",
                qty_usdt=Decimal("1000"),
                market_rate_rub=Decimal("87"),
                client_rate_rub=Decimal("87.6"),
                actor_tg_user_id=_MANAGER_TG_ID,
                chat_id=_CHAT_ID,
                message_id=94,
                reason="Повторное исправление",
            )
        )


def _command(
    *,
    message_id: int,
    operation: BestChangeOperation,
    qty: str,
    market_rate: str,
    client_rate: str,
    deal_at: date = date(2026, 8, 31),
) -> RecordBestChangeDeal:
    return RecordBestChangeDeal(
        operation=operation,
        city="члб",
        qty_usdt=Decimal(qty),
        market_rate_rub=Decimal(market_rate),
        client_rate_rub=Decimal(client_rate),
        actor_tg_user_id=_MANAGER_TG_ID,
        chat_id=_CHAT_ID,
        message_id=message_id,
        deal_at=deal_at,
    )


@pytest.mark.asyncio
async def test_best_change_month_report_and_close_are_atomic_and_idempotent(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users(tg_user_id, display_name, role)
            VALUES ($1, 'BestChange Manager', 'manager')
            ON CONFLICT (tg_user_id) DO UPDATE SET is_active = TRUE
            """,
            _MANAGER_TG_ID,
        )
    service = BestChangeService(
        lambda: AsyncpgUnitOfWork(pool),
        chat_id=_CHAT_ID,
        today_factory=lambda: date(2026, 9, 1),
    )
    await service.record(
        RecordBestChangeDeal(
            operation=BestChangeOperation.SALE,
            city="члб",
            qty_usdt=Decimal("1000"),
            market_rate_rub=Decimal("87"),
            client_rate_rub=Decimal("88"),
            actor_tg_user_id=_MANAGER_TG_ID,
            chat_id=_CHAT_ID,
            message_id=201,
            deal_at=date(2026, 8, 20),
        )
    )
    await service.record(
        RecordBestChangeDeal(
            operation=BestChangeOperation.PURCHASE,
            city="тюм",
            qty_usdt=Decimal("500"),
            market_rate_rub=Decimal("88"),
            client_rate_rub=Decimal("87"),
            actor_tg_user_id=_MANAGER_TG_ID,
            chat_id=_CHAT_ID,
            message_id=202,
            deal_at=date(2026, 8, 21),
        )
    )

    preliminary = await service.month_report(date(2026, 8, 1))
    command = CloseBestChangeMonth(
        period_month=date(2026, 8, 1),
        actor_tg_user_id=_MANAGER_TG_ID,
        chat_id=_CHAT_ID,
        message_id=203,
    )
    first = await service.close_month(command)
    partner_payment = RecordBestChangePayment(
        period_month=date(2026, 8, 1),
        kind=BestChangePaymentKind.PARTNER,
        amount=Decimal("525"),
        payment_reference="cash-order-17",
        actor_tg_user_id=_MANAGER_TG_ID,
        chat_id=_CHAT_ID,
        message_id=204,
    )
    coindrop_payment = RecordBestChangePayment(
        period_month=date(2026, 8, 1),
        kind=BestChangePaymentKind.COINDROP,
        amount=Decimal("2"),
        payment_reference="tx-hash-42",
        actor_tg_user_id=_MANAGER_TG_ID,
        chat_id=_CHAT_ID,
        message_id=205,
    )
    paid_partner = await service.record_payment(partner_payment)
    paid_coindrop = await service.record_payment(coindrop_payment)
    repeated_coindrop = await service.record_payment(coindrop_payment)
    with pytest.raises(ValueError, match="exceeds the remaining amount"):
        await service.record_payment(
            RecordBestChangePayment(
                period_month=date(2026, 8, 1),
                kind=BestChangePaymentKind.PARTNER,
                amount=Decimal("0.01"),
                payment_reference="cash-order-overpayment",
                actor_tg_user_id=_MANAGER_TG_ID,
                chat_id=_CHAT_ID,
                message_id=206,
            )
        )
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE deals SET status = 'canceled' WHERE id = $1",
            preliminary.deal_ids[0],
        )
    repeated = await service.close_month(command)
    async with pool.acquire() as connection:
        await connection.execute(
            "UPDATE deals SET status = 'done' WHERE id = $1",
            preliminary.deal_ids[0],
        )
    reverse_month_command = ReverseBestChangeMonth(
        period_month=date(2026, 8, 1),
        actor_tg_user_id=_MANAGER_TG_ID,
        chat_id=_CHAT_ID,
        message_id=207,
        reason="Исправление месячного расчёта",
    )
    with pytest.raises(DomainStateError, match="Reverse all BestChange payments"):
        await service.reverse_month(reverse_month_command)
    reversed_partner = await service.reverse_payment(
        ReverseBestChangePayment(
            payment_id=paid_partner.payment_id,
            actor_tg_user_id=_MANAGER_TG_ID,
            chat_id=_CHAT_ID,
            message_id=208,
            reason="Ошибочная выплата партнёру",
        )
    )
    reverse_coindrop_command = ReverseBestChangePayment(
        payment_id=paid_coindrop.payment_id,
        actor_tg_user_id=_MANAGER_TG_ID,
        chat_id=_CHAT_ID,
        message_id=209,
        reason="Ошибочный tx hash",
    )
    reversed_coindrop = await service.reverse_payment(reverse_coindrop_command)
    repeated_reversal = await service.reverse_payment(reverse_coindrop_command)
    reversed_month = await service.reverse_month(reverse_month_command)
    repeated_month_reversal = await service.reverse_month(reverse_month_command)

    assert preliminary.purchase_tym_rub == Decimal("350.00")
    assert preliminary.sale_chlb_rub == Decimal("700.00")
    assert preliminary.profit_pool_rub == Decimal("1050.00")
    assert preliminary.partner_share_rub == Decimal("525.00")
    assert preliminary.skyex_profit_rub == Decimal("525.00")
    assert preliminary.platform_fee_accrued_usdt == Decimal("5.133229")
    assert preliminary.deal_count == 2
    assert preliminary.closure_id is None
    assert first.repeated is False
    assert first.report.closure_id is not None
    assert first.report.closed_by_tg_user_id == _MANAGER_TG_ID
    assert paid_partner.report.partner_paid_rub == Decimal("525.00")
    assert paid_partner.report.partner_delta_rub == 0
    assert paid_coindrop.report.platform_fee_paid_usdt == Decimal("2.000000")
    assert paid_coindrop.report.platform_fee_delta_usdt == Decimal("3.133229")
    assert repeated_coindrop.repeated is True
    assert repeated_coindrop.payment_id == paid_coindrop.payment_id
    assert repeated.repeated is True
    assert repeated.report.closure_id == first.report.closure_id
    assert repeated.report.profit_pool_rub == Decimal("1050.00")
    assert reversed_partner.report.partner_paid_rub == 0
    assert reversed_coindrop.report.platform_fee_paid_usdt == 0
    assert repeated_reversal.repeated is True
    assert repeated_reversal.reversal_payment_id == (
        reversed_coindrop.reversal_payment_id
    )
    assert reversed_month.repeated is False
    assert repeated_month_reversal.repeated is True
    assert repeated_month_reversal.reversal_closure_id == (
        reversed_month.reversal_closure_id
    )
    balances = await service.balances()
    assert balances[BestChangeAccount.PURCHASE_TYM] == Decimal("350.00000000")
    assert balances[BestChangeAccount.SALE_CHLB] == Decimal("700.00000000")
    assert balances[BestChangeAccount.PLATFORM_FEE] == Decimal("5.13322900")
    async with pool.acquire() as connection:
        closure_count = await connection.fetchval(
            "SELECT COUNT(*) FROM best_change_month_closures"
        )
        close_moves = await connection.fetch(
            """
            SELECT account_code, amount, closure_id
            FROM best_change_account_moves
            WHERE closure_id IS NOT NULL ORDER BY id
            """
        )
        payments = await connection.fetch(
            """
            SELECT payment_kind, currency_code, amount, payment_reference
            FROM best_change_payments ORDER BY id
            """
        )
    assert closure_count == 2
    assert [(row["account_code"], row["amount"]) for row in close_moves] == [
        ("purchase_tym", Decimal("-350.00000000")),
        ("sale_chlb", Decimal("-700.00000000")),
        ("platform_fee", Decimal("-2.00000000")),
        ("platform_fee", Decimal("2.00000000")),
        ("purchase_tym", Decimal("350.00000000")),
        ("sale_chlb", Decimal("700.00000000")),
    ]
    assert {row["closure_id"] for row in close_moves} == {
        first.report.closure_id,
        reversed_month.reversal_closure_id,
    }
    assert [tuple(row.values()) for row in payments] == [
        ("partner", "RUB", Decimal("525.00000000"), "cash-order-17"),
        ("coindrop", "USDT", Decimal("2.00000000"), "tx-hash-42"),
        ("partner", "RUB", Decimal("-525.00000000"), "cash-order-17"),
        ("coindrop", "USDT", Decimal("-2.00000000"), "tx-hash-42"),
    ]
    reclosed = await service.close_month(
        CloseBestChangeMonth(
            period_month=date(2026, 8, 1),
            actor_tg_user_id=_MANAGER_TG_ID,
            chat_id=_CHAT_ID,
            message_id=210,
        )
    )
    assert reclosed.repeated is False
    assert reclosed.report.closure_id != first.report.closure_id
    assert (await service.balances())[BestChangeAccount.PURCHASE_TYM] == 0


@pytest.mark.asyncio
async def test_best_change_cannot_close_current_month(pool) -> None:
    service = BestChangeService(
        lambda: AsyncpgUnitOfWork(pool),
        chat_id=_CHAT_ID,
        today_factory=lambda: date(2026, 9, 1),
    )

    with pytest.raises(ValueError, match="completed BestChange month"):
        await service.close_month(
            CloseBestChangeMonth(
                period_month=date(2026, 9, 1),
                actor_tg_user_id=_MANAGER_TG_ID,
                chat_id=_CHAT_ID,
                message_id=204,
            )
        )
