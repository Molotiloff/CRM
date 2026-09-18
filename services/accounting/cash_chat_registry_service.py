from __future__ import annotations

import logging
from collections.abc import Mapping

from domain import DomainValidationError

from .models import CashChatBinding, CashChatRegistrySyncResult
from .ports import CashChatRegistryRepositoryPort

log = logging.getLogger(__name__)


class CashChatRegistrySyncService:
    """Synchronize configured city cash chats without moving ledger balances."""

    def __init__(
        self,
        repository: CashChatRegistryRepositoryPort,
        *,
        city_cash_chats: Mapping[str, int],
        moscow_rub_cash_chats: Mapping[str, int] | None = None,
        request_chat_ids: frozenset[int] = frozenset(),
    ) -> None:
        self._repository = repository
        self._bindings = self._build_bindings(
            city_cash_chats,
            moscow_rub_cash_chats or {},
        )
        overlaps = {binding.chat_id for binding in self._bindings} & request_chat_ids
        if overlaps:
            cities = sorted(
                binding.city for binding in self._bindings if binding.chat_id in overlaps
            )
            raise DomainValidationError(
                "City cash chats must differ from request chats: " + ", ".join(cities)
            )

    async def sync(self) -> CashChatRegistrySyncResult:
        result = await self._repository.sync_configured(self._bindings)
        log.info(
            "City cash registry synchronized (cities=%s configured=%d inserted=%d "
            "reactivated=%d deactivated=%d)",
            [binding.city for binding in self._bindings],
            result.configured,
            result.inserted,
            result.reactivated,
            result.deactivated,
        )
        return result

    @staticmethod
    def _build_bindings(
        city_cash_chats: Mapping[str, int],
        moscow_rub_cash_chats: Mapping[str, int],
    ) -> tuple[CashChatBinding, ...]:
        bindings: list[CashChatBinding] = []
        seen_chat_ids: set[int] = set()
        for raw_city, raw_chat_id in city_cash_chats.items():
            city = str(raw_city).strip().lower()
            chat_id = int(raw_chat_id)
            if not city:
                raise DomainValidationError("City cash chat city is required")
            if chat_id == 0:
                raise DomainValidationError("City cash chat id must not be zero")
            if chat_id in seen_chat_ids:
                raise DomainValidationError("City cash chat id is assigned more than once")
            seen_chat_ids.add(chat_id)
            bindings.append(
                CashChatBinding(
                    city=city,
                    chat_id=chat_id,
                    location_name=f"Городская касса: {city}",
                )
            )
        for location_name, raw_chat_id in moscow_rub_cash_chats.items():
            chat_id = int(raw_chat_id)
            if chat_id == 0:
                raise DomainValidationError("Moscow RUB cash chat id must not be zero")
            if chat_id in seen_chat_ids:
                raise DomainValidationError("Cash chat id is assigned more than once")
            seen_chat_ids.add(chat_id)
            bindings.append(
                CashChatBinding(
                    city="мск",
                    chat_id=chat_id,
                    location_name=str(location_name).strip(),
                    cash_currency_codes=("RUB",),
                )
            )
        return tuple(
            sorted(bindings, key=lambda binding: (binding.city, binding.location_name))
        )
