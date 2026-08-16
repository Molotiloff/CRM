from __future__ import annotations

from dataclasses import dataclass

from domain import CashRequestKind, DomainValidationError, Money


@dataclass(frozen=True, slots=True)
class CashRequestCommand:
    chat_id: int


@dataclass(frozen=True, slots=True)
class CashRequestStatusCommand(CashRequestCommand):
    message_id: int
    card_text: str
    is_caption: bool
    callback_data: str


@dataclass(frozen=True, slots=True)
class CashRequestDetails:
    kind: CashRequestKind
    money: Money | None = None
    receive: Money | None = None
    pay: Money | None = None

    def __post_init__(self) -> None:
        single = self.kind in {CashRequestKind.DEPOSIT, CashRequestKind.WITHDRAWAL}
        if single and (self.money is None or self.receive is not None or self.pay is not None):
            raise DomainValidationError("Invalid single-currency cash request")
        if not single and (self.money is not None or self.receive is None or self.pay is None):
            raise DomainValidationError("Invalid FX cash request")


@dataclass(frozen=True, slots=True)
class CashRequestResult:
    ok: bool
    req_id: str | None = None
    error: str | None = None
    client_text: str | None = None
    request_text: str | None = None
    schedule_line: str | None = None
    removed_from_schedule: bool = False
