from datetime import UTC, datetime
from decimal import Decimal

import pytest

from domain import (
    CashDealBody,
    CashRequestKind,
    CityCode,
    CurrencyCode,
    Deal,
    DealBody,
    DealStatus,
    DealTransitionPolicy,
    DomainValidationError,
    ExchangeDealBody,
    ExchangeRequestSource,
    ExchangeRequestStatus,
    InvalidDealTransitionError,
    Money,
    ScheduleEntry,
    SourceKind,
    TelegramMessageRef,
)


def test_currency_and_city_codes_are_normalized() -> None:
    assert str(CurrencyCode(" rub ")) == "RUB"
    assert str(CityCode("  Нижний   Новгород ")) == "нижний новгород"


@pytest.mark.parametrize("value", ["", "US DT", "USDT!"])
def test_currency_code_rejects_invalid_value(value: str) -> None:
    with pytest.raises(DomainValidationError, match="Invalid currency code"):
        CurrencyCode(value)


def test_money_uses_decimal_precision_and_requires_positive_amount() -> None:
    money = Money.from_raw("12.345", "usdt", precision=2)

    assert money.amount == Decimal("12.35")
    assert money.currency == CurrencyCode("USDT")
    assert money.precision == 2

    with pytest.raises(DomainValidationError, match="greater than zero"):
        Money.from_raw("0", "RUB").require_positive()


def test_telegram_message_ref_coerces_ids_and_rejects_invalid_message() -> None:
    assert TelegramMessageRef("-100", "42") == TelegramMessageRef(-100, 42)

    with pytest.raises(DomainValidationError, match="greater than zero"):
        TelegramMessageRef(-100, 0)


def test_exchange_source_normalizes_domain_values() -> None:
    source = ExchangeRequestSource(
        request_id=" 51374723 ",
        table_request_id="101",
        receive=Money.from_raw("9000.00", "rub"),
        pay=Money.from_raw("100", "usdt"),
        rate=Decimal("90"),
        status=ExchangeRequestStatus.ACTIVE,
        client_message=TelegramMessageRef("-100", "44"),
    )

    assert source.request_id == "51374723"
    assert source.receive.currency == CurrencyCode("RUB")
    assert source.receive.amount == Decimal("9000.00")
    assert source.status is ExchangeRequestStatus.ACTIVE
    assert source.primary_message == TelegramMessageRef(-100, 44)


def test_exchange_source_requires_telegram_reference() -> None:
    with pytest.raises(DomainValidationError, match="reference is missing"):
        ExchangeRequestSource(
            request_id="1",
            table_request_id="1",
            receive=Money.from_raw("1", "RUB"),
            pay=Money.from_raw("1", "USDT"),
            rate=Decimal("1"),
            status=ExchangeRequestStatus.ACTIVE,
        )


def test_schedule_entry_normalizes_domain_values() -> None:
    entry = ScheduleEntry(
        request_id="Б-123456",
        city=" ЕКБ ",
        kind="dep",
        line_text="+1000 RUB - Client",
        client_name="Client",
        request_message=TelegramMessageRef(-777, 55),
        hhmm="10:00",
    )

    assert entry.city == CityCode("екб")
    assert entry.kind is CashRequestKind.DEPOSIT
    assert entry.request_message == TelegramMessageRef(-777, 55)


def test_deal_maps_record_to_typed_aggregate() -> None:
    now = datetime.now(UTC)
    deal = Deal.from_record(
        {
            "id": "7",
            "deal_no": "100007",
            "deal_type": "purchase",
            "city": " ЕКБ ",
            "status": "fixed",
            "source": "tg_bot",
            "source_kind": "exchange",
            "body": {"recv_code": "RUB", "rate": "90"},
            "created_at": now,
            "updated_at": now,
            "status_events": [
                {
                    "id": 1,
                    "old_status": "new",
                    "new_status": "fixed",
                    "actor_name": "Manager",
                    "payload": {"rate": "90"},
                    "created_at": now,
                }
            ],
        }
    )

    assert deal.id == 7
    assert deal.city == CityCode("екб")
    assert deal.status is DealStatus.FIXED
    assert deal.body.get("recv_code") == "RUB"
    assert deal.status_events[0].new_status is DealStatus.FIXED


def test_deal_body_copies_input_and_rejects_non_mapping() -> None:
    source = {"amount": "100"}
    body = DealBody(source)
    source["amount"] = "200"

    assert body.get("amount") == "100"
    assert body.merged({"amount": "150"}).get("amount") == "150"

    with pytest.raises(DomainValidationError, match="must be an object"):
        DealBody("invalid")


def test_exchange_deal_body_exposes_money_and_preserves_extension_fields() -> None:
    body = ExchangeDealBody.from_body(
        DealBody(
            {
                "client_req_id": "51374723",
                "recv_code": "rub",
                "recv_amount": "9000.00",
                "pay_code": "usdt",
                "pay_amount": "100",
                "rate": "90",
                "custom": "preserved",
            }
        )
    )

    updated = body.with_edit(
        receive=Money.from_raw("13500", "RUB"),
        pay=Money.from_raw("150", "USDT"),
        rate=Decimal("90"),
        note="changed",
        operation_id=501,
    )

    assert body.receive == Money.from_raw("9000.00", "RUB")
    assert body.pay.currency == CurrencyCode("USDT")
    assert updated.get("pay_amount") == "150"
    assert updated.get("source_operation_id") == 501
    assert updated.get("custom") == "preserved"


@pytest.mark.parametrize(
    ("values", "kind"),
    [
        (
            {
                "req_id": "Б-123456",
                "request_kind": "dep",
                "currency": "rub",
                "amount": "1000",
            },
            CashRequestKind.DEPOSIT,
        ),
        (
            {
                "req_id": "О-123456",
                "request_kind": "fx",
                "in_code": "rub",
                "in_amount": "9000",
                "out_code": "usdt",
                "out_amount": "100",
            },
            CashRequestKind.EXCHANGE,
        ),
    ],
)
def test_cash_deal_body_validates_financial_shape(values, kind) -> None:
    body = CashDealBody.from_body(DealBody(values))

    assert body.kind is kind
    if kind is CashRequestKind.DEPOSIT:
        assert body.money == Money.from_raw("1000", "RUB")
    else:
        assert body.receive == Money.from_raw("9000", "RUB")
        assert body.pay == Money.from_raw("100", "USDT")


def test_cash_deal_body_rejects_incomplete_financial_shape() -> None:
    with pytest.raises(DomainValidationError, match="cash pay currency"):
        CashDealBody.from_body(
            DealBody(
                {
                    "req_id": "О-123456",
                    "request_kind": "fx",
                    "in_code": "RUB",
                    "in_amount": "9000",
                }
            )
        )


def test_typed_deal_bodies_reject_invalid_direct_construction() -> None:
    with pytest.raises(DomainValidationError, match="amounts must be Money"):
        ExchangeDealBody(
            request_id="1",
            receive="100",
            pay=Money.from_raw("1", "USDT"),
            rate=Decimal("100"),
            client_name=None,
            note=None,
            source_operation_id=None,
            _body=DealBody({}),
        )

    with pytest.raises(DomainValidationError, match="single-currency"):
        CashDealBody(
            request_id="Б-1",
            kind=CashRequestKind.DEPOSIT,
            client_name=None,
            client_text=None,
            request_text=None,
            client_message=None,
            money=None,
            receive=None,
            pay=None,
            note=None,
            _body=DealBody({}),
        )


def test_deal_rejects_unknown_status_at_mapping_boundary() -> None:
    with pytest.raises(DomainValidationError, match="Invalid deal status"):
        Deal.from_record(
            {
                "id": 1,
                "deal_no": 100001,
                "deal_type": "sale",
                "city": "екб",
                "status": "unknown",
                "source": "crm",
                "body": {},
            }
        )


def test_deal_transition_policy_owns_source_specific_status_graph() -> None:
    assert DealTransitionPolicy.allowed(DealStatus.NEW, SourceKind.EXCHANGE) == (
        DealStatus.FIXED,
        DealStatus.CANCELED,
    )

    with pytest.raises(InvalidDealTransitionError, match="new -> done"):
        DealTransitionPolicy.validate(
            DealStatus.NEW,
            DealStatus.DONE,
            SourceKind.EXCHANGE,
        )
