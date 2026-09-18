from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable

from db_asyncpg.ports.workflows import CashRequestContextRepositoryPort
from observability import NULL_METRICS, MetricsRecorder
from services.cash_requests.calculator import CashRequestCalculator
from services.cash_requests.card_parser import CashCardParser
from services.cash_requests.card_presenter import CashCardPresenter
from services.cash_requests.keyboard_port import NullCashKeyboardPresenter
from services.cash_requests.models import RequestContext, ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.cash_requests.schedule_coordinator import CashScheduleCoordinator
from services.crm.telegram_deal_registrar import TelegramDealRegistrarPort
from services.messaging import MessengerError, MessengerPort

ScheduleBoardSync = Callable[[str], Awaitable[None]]
log = logging.getLogger(__name__)


class CashRequestUseCaseBase:
    def __init__(
        self,
        *,
        repo: CashRequestContextRepositoryPort,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
        deal_registrar: TelegramDealRegistrarPort | None = None,
        calculator: CashRequestCalculator | None = None,
        card_presenter: CashCardPresenter | None = None,
        card_parser: CashCardParser | None = None,
        schedule_coordinator: CashScheduleCoordinator | None = None,
        metrics: MetricsRecorder = NULL_METRICS,
    ) -> None:
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service
        self.deal_registrar = deal_registrar
        self.calculator = calculator or CashRequestCalculator()
        self.card_presenter = card_presenter or CashCardPresenter(NullCashKeyboardPresenter())
        self.card_parser = card_parser or CashCardParser()
        self.schedule_coordinator = schedule_coordinator or CashScheduleCoordinator(
            router_service=router_service,
            schedule_service=schedule_service,
        )
        self._metrics = metrics

    @staticmethod
    def _gen_req_id() -> str:
        return f"Б-{random.randint(0, 999999):06d}"

    @staticmethod
    def _gen_pin() -> str:
        return f"{random.randint(100, 999)}-{random.randint(100, 999)}"

    async def _build_request_context_core(
        self,
        *,
        chat_id: int,
        chat_name: str,
        city: str,
        client_group: str | None = None,
    ) -> RequestContext:
        client_id = await self.repo.ensure_client(
            chat_id=chat_id,
            name=chat_name,
            client_group=client_group,
        )
        return RequestContext(
            city=(city or self.router_service.default_city).strip().lower(),
            request_chat_id=self.router_service.pick_request_chat_for_city(city),
            chat_name=chat_name,
            client_id=client_id,
        )

    async def _sync_schedule_core(
        self,
        *,
        req_id: str,
        city: str,
        line_text: str,
        request_kind: str,
        client_name: str,
        request_chat_id: int,
        request_message_id: int,
        hhmm: str | None = None,
        sync_schedule_board: ScheduleBoardSync | None = None,
    ) -> None:
        if not line_text:
            return

        await self.schedule_coordinator.upsert(
            ScheduleEntry(
                req_id=req_id,
                city=city,
                hhmm=hhmm,
                request_kind=request_kind,
                line_text=line_text,
                client_name=client_name,
                request_chat_id=request_chat_id,
                request_message_id=request_message_id,
            ),
            sync_board=sync_schedule_board,
        )

    async def _edit_request_chat_message_core(
        self,
        *,
        messenger: MessengerPort,
        req_id: str,
        text_city: str,
        city_markup,
    ) -> tuple[int, int] | None:
        old_entry = await self.repo.get_request_schedule_entry_by_req_id(req_id=req_id)
        if not old_entry:
            return None

        old_chat_id = old_entry.get("request_chat_id")
        old_message_id = old_entry.get("request_message_id")
        if not old_chat_id or not old_message_id:
            return None

        try:
            await messenger.edit_text(
                chat_id=int(old_chat_id),
                message_id=int(old_message_id),
                text=text_city,
                reply_markup=city_markup,
            )
            return int(old_chat_id), int(old_message_id)
        except MessengerError as e:
            log.warning("Failed to edit request-chat message for req %s: %r", req_id, e)
            return None
