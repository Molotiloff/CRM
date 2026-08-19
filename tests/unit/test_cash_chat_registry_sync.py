from __future__ import annotations

import pytest

from domain import DomainValidationError
from services.accounting.cash_chat_registry_service import CashChatRegistrySyncService
from services.accounting.models import CashChatBinding, CashChatRegistrySyncResult


class RecordingCashRegistry:
    def __init__(self) -> None:
        self.bindings: tuple[CashChatBinding, ...] | None = None

    async def sync_configured(
        self,
        bindings: tuple[CashChatBinding, ...],
    ) -> CashChatRegistrySyncResult:
        self.bindings = bindings
        return CashChatRegistrySyncResult(
            configured=len(bindings),
            inserted=len(bindings),
            reactivated=0,
            deactivated=0,
        )


async def test_cash_chat_sync_normalizes_cities_without_exposing_chat_ids() -> None:
    repository = RecordingCashRegistry()
    service = CashChatRegistrySyncService(
        repository,
        city_cash_chats={" ЧЛБ ": -200, "екб": -100},
    )

    result = await service.sync()

    assert result.configured == 2
    assert repository.bindings == (
        CashChatBinding(city="екб", chat_id=-100, location_name="Городская касса: екб"),
        CashChatBinding(city="члб", chat_id=-200, location_name="Городская касса: члб"),
    )


def test_cash_chat_sync_rejects_request_chat_overlap() -> None:
    with pytest.raises(DomainValidationError, match="must differ.*екб"):
        CashChatRegistrySyncService(
            RecordingCashRegistry(),
            city_cash_chats={"екб": -100},
            request_chat_ids=frozenset({-100}),
        )


def test_cash_chat_sync_rejects_duplicate_chat_ids() -> None:
    with pytest.raises(DomainValidationError, match="more than once"):
        CashChatRegistrySyncService(
            RecordingCashRegistry(),
            city_cash_chats={"екб": -100, "члб": -100},
        )
