"""card_parser.parse_get_give — от него зависят cancel/edit денежные пути."""
from __future__ import annotations

from decimal import Decimal

from services.exchange.card_parser import (
    extract_request_id,
    parse_amount_code,
    parse_get_give,
)


class TestParseAmountCode:
    def test_plain(self) -> None:
        assert parse_amount_code("100 USDT") == (Decimal("100"), "USDT")

    def test_thousand_separators_and_comma(self) -> None:
        assert parse_amount_code("1 234 567,89 rub") == (Decimal("1234567.89"), "RUB")
        assert parse_amount_code("1'000 usdt") == (Decimal("1000"), "USDT")
        assert parse_amount_code("9 000 RUB") == (Decimal("9000"), "RUB")

    def test_garbage_returns_none(self) -> None:
        assert parse_amount_code("USDT") is None
        assert parse_amount_code("abc usdt") is None


class TestParseGetGive:
    CARD = (
        "Заявка: <code>12345678</code>\n"
        "Получаем: <code>100 USDT</code>\n"
        "Отдаём: <code>9 000 RUB</code>\n"
    )

    def test_parses_card(self) -> None:
        parsed = parse_get_give(self.CARD)
        assert parsed is not None
        (recv_amt, recv_code), (pay_amt, pay_code) = parsed
        assert (recv_amt, recv_code) == (Decimal("100"), "USDT")
        assert (pay_amt, pay_code) == (Decimal("9000"), "RUB")

    def test_plain_text_without_html(self) -> None:
        parsed = parse_get_give("Получаем: 10 EUR\nОтдаём: 11 USDT")
        assert parsed is not None
        assert parsed[0] == (Decimal("10"), "EUR")

    def test_missing_lines_return_none(self) -> None:
        assert parse_get_give("Получаем: 100 USDT") is None
        assert parse_get_give("") is None

    def test_request_id_extraction(self) -> None:
        assert extract_request_id(self.CARD) == "12345678"
        assert extract_request_id("нет номера") is None
