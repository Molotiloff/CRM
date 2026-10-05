from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from api.telegram_links import normalize_telegram_invite_link
from db_asyncpg.ports.administration import ClientInviteLinkRepository as ClientInviteLinkRepository
from db_asyncpg.ports.administration import ClientInviteTarget
from services.lifecycle import ManagedTaskLifecycle

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClientChatLinkInfo:
    is_group: bool
    username: str | None


class InviteLinkUnavailable(Exception):
    """Telegram cannot provide a link for this client chat."""


class InviteLinkRateLimited(InviteLinkUnavailable):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Telegram requested a {retry_after}s retry delay")
        self.retry_after = retry_after


class ClientInviteLinkGateway(Protocol):
    async def get_chat_info(self, chat_id: int) -> ClientChatLinkInfo: ...

    async def create_approval_link(self, chat_id: int) -> str: ...


class ClientInviteLinkBackfill(ManagedTaskLifecycle):
    """Fill missing links without delaying polling or exposing existing primary links."""

    def __init__(
        self,
        *,
        gateway: ClientInviteLinkGateway,
        repository: ClientInviteLinkRepository,
        interval_seconds: float = 3600,
        per_chat_delay_seconds: float = 0.5,
        batch_size: int = 100,
    ) -> None:
        super().__init__(task_name="client_invite_link_backfill")
        self._gateway = gateway
        self._repository = repository
        self._interval_seconds = interval_seconds
        self._per_chat_delay_seconds = per_chat_delay_seconds
        self._batch_size = batch_size

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Client invite-link backfill failed; will retry later")
            await asyncio.sleep(self._interval_seconds)

    async def run_once(self) -> int:
        after_client_id = 0
        saved = 0
        while True:
            targets = await self._repository.list_missing(
                after_client_id=after_client_id,
                limit=self._batch_size,
            )
            if not targets:
                break
            for target in targets:
                after_client_id = target.client_id
                try:
                    link = await self._get_or_create_link(target)
                    if link and await self._repository.save_if_missing(target, link):
                        saved += 1
                except asyncio.CancelledError:
                    raise
                except InviteLinkRateLimited as exc:
                    log.warning(
                        "Telegram throttled invite-link lookup for client_id=%s; retry in %ss",
                        target.client_id,
                        exc.retry_after,
                    )
                    await asyncio.sleep(exc.retry_after + 1)
                except InviteLinkUnavailable as exc:
                    log.info(
                        "Invite link unavailable for client_id=%s chat_id=%s: %s",
                        target.client_id,
                        target.chat_id,
                        exc,
                    )
                except Exception:
                    log.exception(
                        "Invite-link backfill failed for client_id=%s chat_id=%s",
                        target.client_id,
                        target.chat_id,
                    )
                await asyncio.sleep(self._per_chat_delay_seconds)
            if len(targets) < self._batch_size:
                break
        if saved:
            log.info("Stored Telegram links for %s client chats", saved)
        return saved

    async def _get_or_create_link(self, target: ClientInviteTarget) -> str | None:
        chat = await self._gateway.get_chat_info(target.chat_id)
        if not chat.is_group:
            return None
        if chat.username:
            return normalize_telegram_invite_link(f"https://t.me/{chat.username}")
        # Never publish a chat's existing primary link: it may allow joining
        # without approval. Additional links do not revoke the primary link.
        created = await self._gateway.create_approval_link(target.chat_id)
        return normalize_telegram_invite_link(created)
