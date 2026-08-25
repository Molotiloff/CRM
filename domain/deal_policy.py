from __future__ import annotations

from .errors import DomainValidationError
from .value_objects import DealStatus, SourceKind


class InvalidDealTransitionError(DomainValidationError):
    pass


class DealTransitionPolicy:
    _ORDER = (
        DealStatus.FIXED,
        DealStatus.BALANCE_CHECK,
        DealStatus.AWAITING_PAYMENT,
        DealStatus.IN_DELIVERY,
        DealStatus.READY_FOR_CASH_SETTLEMENT,
        DealStatus.DONE,
        DealStatus.CANCELED,
    )
    _EXCHANGE = {
        DealStatus.NEW: frozenset({DealStatus.FIXED, DealStatus.CANCELED}),
        DealStatus.FIXED: frozenset(
            {DealStatus.BALANCE_CHECK, DealStatus.CANCELED}
        ),
        DealStatus.BALANCE_CHECK: frozenset(
            {DealStatus.AWAITING_PAYMENT, DealStatus.CANCELED}
        ),
        DealStatus.AWAITING_PAYMENT: frozenset(
            {DealStatus.IN_DELIVERY, DealStatus.DONE, DealStatus.CANCELED}
        ),
        DealStatus.IN_DELIVERY: frozenset(
            {DealStatus.DONE, DealStatus.CANCELED}
        ),
        DealStatus.DONE: frozenset(),
        DealStatus.CANCELED: frozenset(),
    }
    _GENERAL = {
        DealStatus.NEW: frozenset(
            {
                DealStatus.FIXED,
                DealStatus.AWAITING_PAYMENT,
                DealStatus.IN_DELIVERY,
                DealStatus.READY_FOR_CASH_SETTLEMENT,
                DealStatus.DONE,
                DealStatus.CANCELED,
            }
        ),
        DealStatus.FIXED: frozenset(
            {
                DealStatus.BALANCE_CHECK,
                DealStatus.AWAITING_PAYMENT,
                DealStatus.CANCELED,
            }
        ),
        DealStatus.BALANCE_CHECK: frozenset(
            {DealStatus.AWAITING_PAYMENT, DealStatus.CANCELED}
        ),
        DealStatus.AWAITING_PAYMENT: frozenset(
            {DealStatus.IN_DELIVERY, DealStatus.DONE, DealStatus.CANCELED}
        ),
        DealStatus.IN_DELIVERY: frozenset(
            {DealStatus.DONE, DealStatus.CANCELED}
        ),
        DealStatus.READY_FOR_CASH_SETTLEMENT: frozenset(
            {DealStatus.DONE, DealStatus.CANCELED}
        ),
        DealStatus.DONE: frozenset(),
        DealStatus.CANCELED: frozenset(),
    }

    @classmethod
    def allowed(
        cls,
        status: DealStatus,
        source_kind: SourceKind | None,
    ) -> tuple[DealStatus, ...]:
        graph = cls._EXCHANGE if source_kind is SourceKind.EXCHANGE else cls._GENERAL
        allowed = graph.get(status, frozenset())
        return tuple(item for item in cls._ORDER if item in allowed)

    @classmethod
    def validate(
        cls,
        old_status: DealStatus,
        new_status: DealStatus,
        source_kind: SourceKind | None,
    ) -> None:
        if old_status is new_status:
            return
        if new_status not in cls.allowed(old_status, source_kind):
            raise InvalidDealTransitionError(
                f"Unsupported deal status transition: {old_status} -> {new_status}"
            )
