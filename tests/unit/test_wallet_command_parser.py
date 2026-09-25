from __future__ import annotations

from decimal import Decimal

import pytest

from handlers.wallets import WalletsHandler
from services.wallets.command_parser import WalletCommandParser


def test_parse_regular_currency_change_without_transport_objects() -> None:
    parser = WalletCommandParser()

    parsed = parser.parse_currency_change(
        "/руб 1000+250 оплата заказа",
        chat_id=100,
    )

    assert parsed is not None
    assert parsed.code == "RUB"
    assert parsed.expr == "1000+250"
    assert parsed.amount == Decimal("1250")
    assert parsed.extra_comment == "оплата заказа"
    assert parsed.is_city_cash is False
    assert parsed.cash_city is None
    assert parsed.client_name_for_transfer == ""


def test_parse_city_cash_transfer_tail() -> None:
    parser = WalletCommandParser(city_cash_chats={"екб": 200})

    parsed = parser.parse_currency_change(
        "/usdt -50 Client Name ! invoice 42",
        chat_id=200,
    )

    assert parsed is not None
    assert parsed.code == "USDT"
    assert parsed.amount == Decimal("-50")
    assert parsed.is_city_cash is True
    assert parsed.cash_city == "екб"
    assert parsed.client_name_for_transfer == "Client Name"
    assert parsed.extra_comment == "invoice 42"


@pytest.mark.parametrize("raw_text", ["", "USDT 100", "/USDT"])
def test_non_currency_command_returns_none(raw_text: str) -> None:
    assert WalletCommandParser().parse_currency_change(raw_text, chat_id=100) is None


@pytest.mark.parametrize(
    ("raw_text", "error"),
    [
        ("/USDT value", "Ошибка в выражении суммы"),
        ("/USDT 0", "Сумма должна быть ненулевой"),
    ],
)
def test_invalid_amount_is_rejected(raw_text: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        WalletCommandParser().parse_currency_change(raw_text, chat_id=100)


@pytest.mark.parametrize("command", ["/отпр 50000", "/отпр1 160000"])
def test_parse_partner_usdt_send_amount(command: str) -> None:
    parsed = WalletCommandParser.parse_partner_usdt_send(command)

    assert parsed is not None
    assert parsed.amount == Decimal(command.split()[1])
    assert parsed.raw_text == command


def test_parse_partner_usdt_send_all() -> None:
    parsed = WalletCommandParser.parse_partner_usdt_send("/баотпр")

    assert parsed is not None
    assert parsed.amount is None


@pytest.mark.parametrize("command", ["/отпр 0", "/отпр -100"])
def test_partner_usdt_send_requires_positive_amount(command: str) -> None:
    with pytest.raises(ValueError, match="положительной"):
        WalletCommandParser.parse_partner_usdt_send(command)


@pytest.mark.parametrize(
    ("command", "amount", "currency", "recipient"),
    [
        ("/перевод 1000 Иван Иванов", Decimal("1000"), "RUB", "Иван Иванов"),
        ("/перевод 2,50 USDT Клиент А", Decimal("2.50"), "USDT", "Клиент А"),
    ],
)
def test_parse_client_transfer(
    command: str, amount: Decimal, currency: str, recipient: str
) -> None:
    assert WalletsHandler._parse_transfer(command) == (amount, currency, recipient)


@pytest.mark.parametrize("command", ["/перевод", "/перевод 0 Иван", "/перевод abc Иван"])
def test_invalid_client_transfer(command: str) -> None:
    with pytest.raises(ValueError):
        WalletsHandler._parse_transfer(command)
