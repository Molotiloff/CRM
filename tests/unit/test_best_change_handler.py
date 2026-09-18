from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.best_change import (
    BestChangeHandler,
    format_best_change_balances,
    format_best_change_result,
    parse_best_change_command,
    parse_best_change_correction_command,
    parse_best_change_date,
    parse_best_change_history_command,
    parse_best_change_payment_command,
    parse_best_change_period,
    parse_best_change_reversal_command,
)
from services.best_change import (
    BestChangeAccount,
    BestChangeCalculation,
    BestChangeOperation,
    BestChangePaymentKind,
    BestChangePostingResult,
    RecordBestChangePayment,
)


def test_parse_best_change_command_accepts_decimal_comma_and_bot_suffix() -> None:
    assert parse_best_change_command(
        "/бест@skyex_bot продажа члб 1000,5 87 88,2"
    ) == (
        BestChangeOperation.SALE,
        "члб",
        Decimal("1000.5"),
        Decimal("87"),
        Decimal("88.2"),
    )


def test_parse_best_change_history_command_requires_explicit_date() -> None:
    assert parse_best_change_history_command(
        "/бест@skyex_bot история 10.08.2026 покупка тюм 1000,5 87 86,2"
    ) == (
        date(2026, 8, 10),
        BestChangeOperation.PURCHASE,
        "тюм",
        Decimal("1000.5"),
        Decimal("87"),
        Decimal("86.2"),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.08.2026", date(2026, 8, 10)),
        ("2026-08-10", date(2026, 8, 10)),
    ],
)
def test_parse_best_change_date(value: str, expected: date) -> None:
    assert parse_best_change_date(value) == expected


def test_parse_best_change_history_command_rejects_missing_date() -> None:
    with pytest.raises(ValueError):
        parse_best_change_history_command(
            "/бест история продажа члб 1000 87 88"
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("08.2026", date(2026, 8, 1)),
        ("2026-08", date(2026, 8, 1)),
    ],
)
def test_parse_best_change_period(value: str, expected: date) -> None:
    assert parse_best_change_period(value) == expected


def test_parse_best_change_period_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="ММ.ГГГГ"):
        parse_best_change_period("август")


def test_parse_best_change_payment_command_keeps_reference() -> None:
    assert parse_best_change_payment_command(
        "/бест выплата coindrop 08.2026 3,123456 tx hash 42"
    ) == (
        BestChangePaymentKind.COINDROP,
        date(2026, 8, 1),
        Decimal("3.123456"),
        "tx hash 42",
    )


@pytest.mark.parametrize(
    ("kind", "raw_amount", "expected"),
    [
        (BestChangePaymentKind.PARTNER, "1.005", Decimal("1.01")),
        (BestChangePaymentKind.COINDROP, "1.0000005", Decimal("1.000001")),
    ],
)
def test_best_change_payment_rounds_only_final_posting(
    kind: BestChangePaymentKind,
    raw_amount: str,
    expected: Decimal,
) -> None:
    command = RecordBestChangePayment(
        period_month=date(2026, 8, 1),
        kind=kind,
        amount=Decimal(raw_amount),
        payment_reference="reference",
        actor_tg_user_id=42,
        chat_id=-100500,
        message_id=17,
    )

    assert command.amount == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "/бест сторно выплата 17 ошибочный tx hash",
            ("payment", 17, "ошибочный tx hash"),
        ),
        (
            "/бест сторно закрытие 08.2026 неверный итог",
            ("month", date(2026, 8, 1), "неверный итог"),
        ),
    ],
)
def test_parse_best_change_reversal_command(
    text: str,
    expected: tuple[str, int | date, str],
) -> None:
    assert parse_best_change_reversal_command(text) == expected


def test_parse_best_change_correction_command_keeps_reason() -> None:
    assert parse_best_change_correction_command(
        "/бест исправить 17 продажа члб 1000 87 88,2 опечатка в курсе"
    ) == (
        17,
        BestChangeOperation.SALE,
        "члб",
        Decimal("1000"),
        Decimal("87"),
        Decimal("88.2"),
        "опечатка в курсе",
    )


@pytest.mark.parametrize(
    "text",
    [
        "/бест продажа члб 1000 87",
        "/бест обмен члб 1000 87 88",
        "/бест продажа члб сто 87 88",
    ],
)
def test_parse_best_change_command_rejects_invalid_input(text: str) -> None:
    with pytest.raises(ValueError):
        parse_best_change_command(text)


def test_format_best_change_loss_has_explicit_warning() -> None:
    calculation = BestChangeCalculation(
        operation=BestChangeOperation.SALE,
        city="члб",
        qty_usdt=Decimal("100"),
        market_rate_rub=Decimal("88"),
        client_rate_rub=Decimal("87"),
        unit_spread_rub=Decimal("-1"),
        gross_spread_rub=Decimal("-100"),
        platform_fee_rub_equivalent=Decimal("0"),
        platform_fee_usdt=Decimal("0.000000"),
        profit_pool_rub=Decimal("-100.00"),
        partner_share_rub=Decimal("-50.00"),
        skyex_profit_rub=Decimal("-50.00"),
        profit_account=BestChangeAccount.SALE_CHLB,
    )
    result = BestChangePostingResult(
        deal_id=17,
        calculation=calculation,
        profit_balance_rub=Decimal("-100"),
        platform_fee_balance_usdt=Decimal("0"),
        repeated=False,
    )

    text = format_best_change_result(result)

    assert "Сделка убыточная" in text
    assert "комиссия CoinDrop не начислена" in text


def test_format_best_change_balances_shows_all_five_accounts() -> None:
    text = format_best_change_balances(
        {
            BestChangeAccount.PURCHASE_TYM: Decimal("1"),
            BestChangeAccount.SALE_TYM: Decimal("2"),
            BestChangeAccount.PURCHASE_CHLB: Decimal("3"),
            BestChangeAccount.SALE_CHLB: Decimal("4"),
            BestChangeAccount.PLATFORM_FEE: Decimal("5.123456"),
        }
    )

    assert "Покупки Тюм: 1.00 RUB" in text
    assert "Продажи Тюм: 2.00 RUB" in text
    assert "Покупки Члб: 3.00 RUB" in text
    assert "Продажи Члб: 4.00 RUB" in text
    assert "Остаток CoinDrop: 5.123456 USDT" in text


@pytest.mark.asyncio
async def test_best_change_wallet_shows_all_accounts() -> None:
    balances = {
        account: Decimal("0")
        for account in BestChangeAccount
    }
    service = SimpleNamespace(balances=AsyncMock(return_value=balances))
    repo = SimpleNamespace(is_manager=AsyncMock(return_value=True))
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100500),
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )
    handler = BestChangeHandler(
        repo,
        service=service,
        chat_id=-100500,
    )

    await handler._cmd_wallet(message)

    service.balances.assert_awaited_once_with()
    sent_text = message.answer.await_args.args[0]
    assert "Покупки Тюм: 0.00 RUB" in sent_text
    assert "Продажи Тюм: 0.00 RUB" in sent_text
    assert "Покупки Члб: 0.00 RUB" in sent_text
    assert "Продажи Члб: 0.00 RUB" in sent_text
    assert "Остаток CoinDrop: 0.000000 USDT" in sent_text


@pytest.mark.asyncio
async def test_best_change_give_shows_only_nonzero_accounts() -> None:
    balances = {
        BestChangeAccount.PURCHASE_TYM: Decimal("0"),
        BestChangeAccount.SALE_TYM: Decimal("12460"),
        BestChangeAccount.PURCHASE_CHLB: Decimal("0"),
        BestChangeAccount.SALE_CHLB: Decimal("0"),
        BestChangeAccount.PLATFORM_FEE: Decimal("61.22449"),
    }
    service = SimpleNamespace(balances=AsyncMock(return_value=balances))
    repo = SimpleNamespace(is_manager=AsyncMock(return_value=True))
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100500),
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )
    handler = BestChangeHandler(
        repo,
        service=service,
        chat_id=-100500,
    )

    await handler._cmd_nonzero(message)

    sent_text = message.answer.await_args.args[0]
    assert "Продажи Тюм: 12460.00 RUB" in sent_text
    assert "Остаток CoinDrop: 61.224490 USDT" in sent_text
    assert "Покупки Тюм" not in sent_text
    assert "Покупки Члб" not in sent_text
    assert "Продажи Члб" not in sent_text


def test_best_change_give_handles_all_zero_balances() -> None:
    balances = {account: Decimal("0") for account in BestChangeAccount}

    assert format_best_change_balances(balances, only_nonzero=True) == (
        "Все счета BestChange нулевые. Посмотреть всё: /кошелек"
    )


@pytest.mark.asyncio
async def test_best_change_history_posts_parsed_date() -> None:
    calculation = BestChangeCalculation(
        operation=BestChangeOperation.SALE,
        city="члб",
        qty_usdt=Decimal("1000"),
        market_rate_rub=Decimal("87"),
        client_rate_rub=Decimal("88"),
        unit_spread_rub=Decimal("1"),
        gross_spread_rub=Decimal("1000"),
        platform_fee_rub_equivalent=Decimal("300"),
        platform_fee_usdt=Decimal("3.409091"),
        profit_pool_rub=Decimal("700"),
        partner_share_rub=Decimal("350"),
        skyex_profit_rub=Decimal("350"),
        profit_account=BestChangeAccount.SALE_CHLB,
    )
    service = SimpleNamespace(
        record=AsyncMock(
            return_value=BestChangePostingResult(
                deal_id=17,
                calculation=calculation,
                profit_balance_rub=Decimal("700"),
                platform_fee_balance_usdt=Decimal("3.409091"),
                repeated=False,
            )
        )
    )
    repo = SimpleNamespace(is_manager=AsyncMock(return_value=True))
    message = SimpleNamespace(
        text="/бест история 10.08.2026 продажа члб 1000 87 88",
        chat=SimpleNamespace(id=-100500),
        from_user=SimpleNamespace(id=42),
        message_id=73,
        date=datetime(2026, 9, 1, tzinfo=UTC),
        answer=AsyncMock(),
    )
    handler = BestChangeHandler(repo, service=service, chat_id=-100500)

    await handler._cmd_best(message)

    command = service.record.await_args.args[0]
    assert command.deal_at == date(2026, 8, 10)
    assert command.comment == "Исторический ввод BestChange"
    assert "Дата исторической сделки: 10.08.2026" in message.answer.await_args.args[0]
