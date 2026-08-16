from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .deals import DealBody
from .errors import DomainValidationError
from .source_models import CashRequestKind
from .value_objects import Money, TelegramMessageRef


@dataclass(frozen=True, slots=True)
class ExchangeDealBody:
    request_id: str
    receive: Money
    pay: Money
    rate: Decimal
    client_name: str | None
    note: str | None
    source_operation_id: int | None
    _body: DealBody

    def __post_init__(self) -> None:
        if not isinstance(self._body, DealBody):
            raise DomainValidationError("Exchange deal body source must be DealBody")
        if not isinstance(self.receive, Money) or not isinstance(self.pay, Money):
            raise DomainValidationError("Exchange deal amounts must be Money")
        request_id = _required_text(self.request_id, "exchange request id")
        self.receive.require_positive()
        self.pay.require_positive()
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "rate", _positive_decimal(self.rate, "exchange rate"))

    @classmethod
    def from_body(cls, body: DealBody) -> ExchangeDealBody:
        values = body.to_dict()
        return cls(
            request_id=_required_text(values.get("client_req_id"), "exchange request id"),
            receive=Money.from_raw(
                values.get("recv_amount"),
                _required_text(values.get("recv_code"), "exchange receive currency"),
            ).require_positive(),
            pay=Money.from_raw(
                values.get("pay_amount"),
                _required_text(values.get("pay_code"), "exchange pay currency"),
            ).require_positive(),
            rate=_positive_decimal(values.get("rate"), "exchange rate"),
            client_name=_optional_text(values.get("client_name")),
            note=_optional_text(values.get("note")),
            source_operation_id=_optional_int(values.get("source_operation_id")),
            _body=body,
        )

    def with_edit(
        self,
        *,
        receive: Money,
        pay: Money,
        rate: Decimal,
        note: str | None,
        operation_id: int,
    ) -> DealBody:
        receive.require_positive()
        pay.require_positive()
        normalized_rate = _positive_decimal(rate, "exchange rate")
        return self._body.merged(
            {
                "recv_code": str(receive.currency),
                "recv_amount": str(receive.amount),
                "pay_code": str(pay.currency),
                "pay_amount": str(pay.amount),
                "rate": format(normalized_rate.normalize(), "f"),
                "note": note,
                "source_operation_id": int(operation_id),
            }
        )


@dataclass(frozen=True, slots=True)
class CashDealBody:
    request_id: str
    kind: CashRequestKind
    client_name: str | None
    client_text: str | None
    request_text: str | None
    client_message: TelegramMessageRef | None
    money: Money | None
    receive: Money | None
    pay: Money | None
    note: str | None
    _body: DealBody

    def __post_init__(self) -> None:
        if not isinstance(self._body, DealBody):
            raise DomainValidationError("Cash deal body source must be DealBody")
        kind = _cash_kind(self.kind)
        request_id = _required_text(self.request_id, "cash request id")
        if kind in {CashRequestKind.DEPOSIT, CashRequestKind.WITHDRAWAL}:
            if self.money is None or self.receive is not None or self.pay is not None:
                raise DomainValidationError("Invalid single-currency cash deal body")
            self.money.require_positive()
        elif self.money is not None or self.receive is None or self.pay is None:
            raise DomainValidationError("Invalid exchange cash deal body")
        else:
            self.receive.require_positive()
            self.pay.require_positive()
        if self.client_message is not None and not isinstance(
            self.client_message, TelegramMessageRef
        ):
            raise DomainValidationError("Invalid cash client Telegram message reference")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "kind", kind)

    @classmethod
    def from_body(cls, body: DealBody) -> CashDealBody:
        values = body.to_dict()
        kind = _cash_kind(values.get("request_kind"))
        money: Money | None = None
        receive: Money | None = None
        pay: Money | None = None
        if kind in {CashRequestKind.DEPOSIT, CashRequestKind.WITHDRAWAL}:
            money = Money.from_raw(
                values.get("amount"),
                _required_text(values.get("currency"), "cash currency"),
            ).require_positive()
        else:
            receive = Money.from_raw(
                values.get("in_amount"),
                _required_text(values.get("in_code"), "cash receive currency"),
            ).require_positive()
            pay = Money.from_raw(
                values.get("out_amount"),
                _required_text(values.get("out_code"), "cash pay currency"),
            ).require_positive()
        return cls(
            request_id=_required_text(values.get("req_id"), "cash request id"),
            kind=kind,
            client_name=_optional_text(values.get("client_name")),
            client_text=_optional_text(values.get("telegram_client_text")),
            request_text=_optional_text(values.get("telegram_request_text")),
            client_message=_optional_message_ref(
                values.get("telegram_client_chat_id"),
                values.get("telegram_client_message_id"),
            ),
            money=money,
            receive=receive,
            pay=pay,
            note=_optional_text(values.get("note")),
            _body=body,
        )

    @property
    def base_text(self) -> str:
        return self.client_text or self.request_text or ""

    def require_client_message(self) -> TelegramMessageRef:
        if self.client_message is None:
            raise DomainValidationError("Cash client Telegram message reference is missing")
        return self.client_message

    def with_single_amount(
        self,
        amount: Decimal,
        *,
        client_text: str | None,
        request_text: str | None,
        note: str | None,
    ) -> DealBody:
        if self.money is None:
            raise DomainValidationError("Cash request does not contain a single amount")
        updated = Money.from_raw(amount, self.money.currency).require_positive()
        return self._body.merged(
            {
                "amount": str(updated.amount),
                "telegram_client_text": client_text,
                "telegram_request_text": request_text,
                "note": note,
            }
        )

    def with_exchange_amounts(
        self,
        receive_amount: Decimal,
        pay_amount: Decimal,
        *,
        client_text: str | None,
        request_text: str | None,
        note: str | None,
    ) -> DealBody:
        if self.receive is None or self.pay is None:
            raise DomainValidationError("Cash request does not contain exchange amounts")
        receive = Money.from_raw(receive_amount, self.receive.currency).require_positive()
        pay = Money.from_raw(pay_amount, self.pay.currency).require_positive()
        return self._body.merged(
            {
                "in_amount": str(receive.amount),
                "out_amount": str(pay.amount),
                "telegram_client_text": client_text,
                "telegram_request_text": request_text,
                "note": note,
            }
        )


def _cash_kind(value: object) -> CashRequestKind:
    try:
        return CashRequestKind(str(value))
    except ValueError:
        raise DomainValidationError(f"Invalid cash request kind: {value!r}") from None


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DomainValidationError(f"Missing {field}")
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DomainValidationError(f"Invalid integer: {value!r}") from None


def _optional_message_ref(chat_id: object, message_id: object) -> TelegramMessageRef | None:
    if chat_id is None and message_id is None:
        return None
    if chat_id is None or message_id is None:
        raise DomainValidationError("Incomplete cash client Telegram message reference")
    return TelegramMessageRef(chat_id, message_id)


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainValidationError(f"Invalid {field}") from None
    if not result.is_finite() or result <= 0:
        raise DomainValidationError(f"Invalid {field}")
    return result
