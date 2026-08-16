from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from domain import (
    CityCode,
    Deal,
    DealBody,
    DealEventPayload,
    DealSource,
    DealStatus,
    DealType,
    DomainStateError,
    DomainValidationError,
    SourceKind,
)
from observability import (
    NULL_METRICS,
    MetricsRecorder,
    bind_log_context,
    logged_operation,
    measured_operation,
)

from .deal_events import DealEvent, DealEventBus

log = logging.getLogger(__name__)


class DealNotFoundError(LookupError):
    pass


class DealValidationError(DomainValidationError):
    pass


class DealStatusConflictError(DomainStateError):
    pass


class _Unset:
    __slots__ = ()


UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class DealCreateCommand:
    deal_type: DealType | str
    city: CityCode | str
    actor_user_id: int | None
    client_id: int | None = None
    counterparty_id: int | None = None
    source: DealSource | str = DealSource.CRM
    comment: str | None = None
    tronscan_url: str | None = None
    body: DealBody | Mapping[str, Any] = field(default_factory=dict)
    profit: Decimal | None = None
    deal_at: date | None = None
    exchange_client_req_id: str | None = None
    source_kind: SourceKind | str | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "deal_type",
            _domain_enum(DealType, self.deal_type, "deal type"),
        )
        object.__setattr__(
            self,
            "city",
            self.city if isinstance(self.city, CityCode) else CityCode(self.city),
        )
        object.__setattr__(
            self,
            "source",
            _domain_enum(DealSource, self.source, "deal source"),
        )
        if self.source_kind is not None:
            object.__setattr__(
                self,
                "source_kind",
                _domain_enum(SourceKind, self.source_kind, "deal source kind"),
            )
        if not isinstance(self.body, DealBody):
            object.__setattr__(self, "body", DealBody(self.body))


@dataclass(frozen=True, slots=True)
class DealListFilter:
    statuses: tuple[DealStatus | str, ...] = ()
    city: CityCode | str | None = None
    deal_type: DealType | str | None = None
    client_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = 200
    offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "statuses",
            tuple(_domain_enum(DealStatus, item, "deal status") for item in self.statuses),
        )
        if self.city is not None and not isinstance(self.city, CityCode):
            object.__setattr__(self, "city", CityCode(self.city))
        if self.deal_type is not None:
            object.__setattr__(
                self,
                "deal_type",
                _domain_enum(DealType, self.deal_type, "deal type"),
            )


@dataclass(frozen=True, slots=True)
class DealUpdateCommand:
    city: CityCode | str | _Unset = UNSET
    client_id: int | None | _Unset = UNSET
    counterparty_id: int | None | _Unset = UNSET
    comment: str | None | _Unset = UNSET
    tronscan_url: str | None | _Unset = UNSET
    body: DealBody | Mapping[str, Any] | _Unset = UNSET
    profit: Decimal | None | _Unset = UNSET
    deal_at: date | _Unset = UNSET

    def __post_init__(self) -> None:
        if not isinstance(self.city, _Unset):
            if self.city is None:
                raise DomainValidationError("Deal city must not be null")
            object.__setattr__(
                self,
                "city",
                self.city if isinstance(self.city, CityCode) else CityCode(self.city),
            )
        if not isinstance(self.body, _Unset) and not isinstance(self.body, DealBody):
            object.__setattr__(self, "body", DealBody(self.body))
        if self.deal_at is None:
            raise DomainValidationError("Deal date must not be null")

    def to_changes(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, _Unset):
                continue
            if isinstance(value, CityCode):
                value = str(value)
            elif isinstance(value, DealBody):
                value = value.to_dict()
            values[item.name] = value
        return values


@dataclass(frozen=True, slots=True)
class DealStatusCommand:
    status: DealStatus | str
    actor_user_id: int | None
    payload: DealEventPayload | Mapping[str, Any] = field(default_factory=dict)
    expected_old_status: DealStatus | str | None = None
    body_patch: DealBody | Mapping[str, Any] = field(default_factory=dict)
    payment_watch_id: int | None = None
    tronscan_url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            _domain_enum(DealStatus, self.status, "deal status"),
        )
        if self.expected_old_status is not None:
            object.__setattr__(
                self,
                "expected_old_status",
                _domain_enum(DealStatus, self.expected_old_status, "expected deal status"),
            )
        if not isinstance(self.payload, DealEventPayload):
            object.__setattr__(self, "payload", DealEventPayload(self.payload))
        if not isinstance(self.body_patch, DealBody):
            object.__setattr__(self, "body_patch", DealBody(self.body_patch))


@dataclass(frozen=True, slots=True)
class DealStatusPreparation:
    payload: DealEventPayload | Mapping[str, Any]
    body_patch: DealBody | Mapping[str, Any] = field(default_factory=dict)
    payment_watch_id: int | None = None
    tronscan_url: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, DealEventPayload):
            object.__setattr__(self, "payload", DealEventPayload(self.payload))
        if not isinstance(self.body_patch, DealBody):
            object.__setattr__(self, "body_patch", DealBody(self.body_patch))


class DealStatusPolicyPort(Protocol):
    async def prepare(
        self,
        deal: Deal,
        *,
        new_status: DealStatus,
        payload: DealEventPayload,
    ) -> DealStatusPreparation: ...


class DealRepositoryPort(Protocol):
    async def list_deals(self, filters: DealListFilter) -> list[Deal]: ...

    async def get_deal(self, deal_id: int) -> Deal | None: ...

    async def create_deal(self, command: DealCreateCommand) -> Deal: ...

    async def create_deal_idempotent(self, command: DealCreateCommand) -> tuple[Deal, bool]: ...

    async def update_deal(self, deal_id: int, command: DealUpdateCommand) -> Deal | None: ...

    async def change_status(
        self,
        deal_id: int,
        command: DealStatusCommand,
    ) -> tuple[Deal, bool] | None: ...


class DealService:
    def __init__(
        self,
        repository: DealRepositoryPort,
        event_bus: DealEventBus,
        status_policy: DealStatusPolicyPort | None = None,
        *,
        metrics: MetricsRecorder = NULL_METRICS,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._status_policy = status_policy
        self._metrics = metrics

    @measured_operation("deal.list")
    async def list_deals(self, filters: DealListFilter) -> list[Deal]:
        self._validate_filters(filters)
        return await self._repository.list_deals(filters)

    @measured_operation("deal.get")
    async def get_deal(self, deal_id: int) -> Deal:
        deal = await self._repository.get_deal(deal_id)
        if deal is None:
            raise DealNotFoundError(f"Deal {deal_id} was not found")
        return deal

    @logged_operation("deal.create")
    @measured_operation("deal.create")
    async def create_deal(self, command: DealCreateCommand) -> Deal:
        deal = await self._repository.create_deal(command)
        self._bind_deal_context(deal)
        await self._publish("deal.created", deal)
        log.info("Deal created", extra={"event": "deal.created"})
        return deal

    @logged_operation("deal.create_source")
    @measured_operation("deal.create_source")
    async def create_source_deal(self, command: DealCreateCommand) -> Deal:
        if not command.source_kind or not command.source_ref:
            raise DealValidationError("Source kind and reference are required")
        deal, created = await self._repository.create_deal_idempotent(command)
        self._bind_deal_context(deal)
        if created:
            await self._publish("deal.created", deal)
            log.info("Source deal created", extra={"event": "deal.created"})
        else:
            log.info("Source deal already exists", extra={"event": "deal.idempotent"})
        return deal

    @logged_operation("deal.update")
    @measured_operation("deal.update")
    async def update_deal(self, deal_id: int, command: DealUpdateCommand) -> Deal:
        bind_log_context(deal_id=deal_id)
        deal = await self._repository.update_deal(deal_id, command)
        if deal is None:
            raise DealNotFoundError(f"Deal {deal_id} was not found")
        self._bind_deal_context(deal)
        await self._publish("deal.updated", deal)
        log.info("Deal updated", extra={"event": "deal.updated"})
        return deal

    @logged_operation("deal.change_status")
    @measured_operation("deal.change_status")
    async def change_status(
        self,
        deal_id: int,
        command: DealStatusCommand,
    ) -> Deal:
        bind_log_context(deal_id=deal_id)
        status_command = command
        if self._status_policy is not None:
            current = await self.get_deal(deal_id)
            prepared = await self._status_policy.prepare(
                current,
                new_status=command.status,
                payload=command.payload,
            )
            status_command = DealStatusCommand(
                status=command.status,
                actor_user_id=command.actor_user_id,
                payload=prepared.payload,
                expected_old_status=current.status,
                body_patch=prepared.body_patch,
                payment_watch_id=prepared.payment_watch_id,
                tronscan_url=prepared.tronscan_url,
            )
            if current.status is command.status:
                if not prepared.body_patch and not prepared.tronscan_url:
                    return current
                deal = await self.update_deal(
                    deal_id,
                    DealUpdateCommand(
                        body=current.body.merged(prepared.body_patch.to_dict()),
                        tronscan_url=(prepared.tronscan_url if prepared.tronscan_url else UNSET),
                    ),
                )
                return deal
        result = await self._repository.change_status(deal_id, status_command)
        if result is None:
            raise DealNotFoundError(f"Deal {deal_id} was not found")
        deal, changed = result
        self._bind_deal_context(deal)
        if changed:
            await self._publish(
                "deal.status_changed",
                deal,
                {"status": str(command.status), **status_command.payload.to_dict()},
            )
            log.info(
                "Deal status changed",
                extra={"event": "deal.status_changed", "status": str(command.status)},
            )
        return deal

    async def _publish(
        self,
        event_type: str,
        deal: Deal,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        payload = {"dealNo": deal.deal_no, "status": str(deal.status)}
        payload.update(extra or {})
        await self._event_bus.publish(
            DealEvent(
                type=event_type,
                deal_id=deal.id,
                payload=DealEventPayload(payload),
            )
        )

    async def publish_committed_update(self, deal: Deal) -> None:
        self._bind_deal_context(deal)
        await self._publish("deal.updated", deal)

    async def publish_committed_status_change(
        self,
        deal: Deal,
        *,
        new_status: str,
        payload: Mapping[str, Any],
    ) -> None:
        self._bind_deal_context(deal)
        await self._publish(
            "deal.status_changed",
            deal,
            {"status": new_status, **payload},
        )

    @staticmethod
    def _bind_deal_context(deal: Deal) -> None:
        bind_log_context(
            deal_id=deal.id,
            request_id=(
                deal.exchange_client_req_id
                or deal.body.get("client_req_id")
                or deal.body.get("req_id")
            ),
        )

    @staticmethod
    def _validate_filters(filters: DealListFilter) -> None:
        if filters.limit < 1 or filters.limit > 500:
            raise DealValidationError("Deal list limit must be between 1 and 500")
        if filters.offset < 0:
            raise DealValidationError("Deal list offset must not be negative")


def _domain_enum(enum_type, value: object, field: str):
    try:
        return enum_type(str(value))
    except ValueError:
        raise DomainValidationError(f"Invalid {field}: {value!r}") from None
