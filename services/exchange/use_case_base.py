from __future__ import annotations

import logging

from db_asyncpg.ports.workflows import ExchangeCommandRepositoryPort
from observability import NULL_METRICS, MetricsRecorder
from services.act_counter import ActCounterService
from services.act_counter.text_builder import ActCounterTextBuilder
from services.crm.telegram_deal_registrar import TelegramDealRegistrarPort
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.keyboard_port import ExchangeKeyboardPort
from services.exchange.notification_builder import ExchangeNotificationBuilder
from services.exchange.request_context import ExchangeRequestContext
from services.exchange.source_link_service import ExchangeSourceLinkService
from services.exchange.text_builder import ExchangeTextBuilder
from services.exchange.transaction_service import ExchangeTransactionService
from services.exchange.wallet_presenter import ExchangeWalletPresenter
from services.messaging import MessengerPort
from services.unit_of_work import UnitOfWorkFactory

log = logging.getLogger(__name__)


class _ExchangeUseCaseBase:
    def __init__(
        self,
        *,
        repo: ExchangeCommandRepositoryPort,
        request_chat_id: int | None,
        balance_service: ExchangeBalanceService,
        calculator: ExchangeCalculator,
        text_builder: ExchangeTextBuilder,
        unit_of_work_factory: UnitOfWorkFactory,
        keyboards: ExchangeKeyboardPort,
        act_counter_service: ActCounterService | None = None,
        deal_registrar: TelegramDealRegistrarPort | None = None,
        transaction_service: ExchangeTransactionService | None = None,
        wallet_presenter: ExchangeWalletPresenter | None = None,
        notification_builder: ExchangeNotificationBuilder | None = None,
        source_links: ExchangeSourceLinkService | None = None,
        metrics: MetricsRecorder = NULL_METRICS,
    ) -> None:
        self.repo = repo
        self.request_chat_id = request_chat_id
        self.calculator = calculator
        self.text_builder = text_builder
        self.act_counter_service = act_counter_service
        self.deal_registrar = deal_registrar
        self.transaction_service = transaction_service or ExchangeTransactionService(
            unit_of_work_factory=unit_of_work_factory,
            balance_service=balance_service,
        )
        self.wallet_presenter = wallet_presenter or ExchangeWalletPresenter()
        self.notification_builder = notification_builder or ExchangeNotificationBuilder()
        if source_links is None:
            raise ValueError("source_links is required")
        self.source_links = source_links
        self.keyboards = keyboards
        self._metrics = metrics
        self.act_text_builder = ActCounterTextBuilder()

    async def _get_exchange_request_meta(self, client_req_id: str) -> ExchangeRequestContext | None:
        """Метаданные привязки заявки (опциональное обогащение) или None.

        «Не найдено» возвращается как None самим запросом (нет строки).
        Операционный сбой БД тоже деградирует в None — вызыватели корректно
        работают без меты, — но, в отличие от «не найдено», логируется с
        трейсбэком, чтобы сбой не оставался невидимым.
        """
        try:
            return await self.source_links.get_context(str(client_req_id))
        except Exception:
            log.exception(
                "Failed to load exchange request meta for client_req_id=%s",
                client_req_id,
            )
            return None

    async def _notify_act_current_amount(
        self, *, messenger: MessengerPort, request_chat_id: int | None
    ) -> None:
        if not self.act_counter_service or not request_chat_id:
            return
        try:
            current_amount = await self.act_counter_service.get_current_amount(
                request_chat_id=int(request_chat_id),
            )
            await messenger.send(
                chat_id=int(request_chat_id),
                text=self.act_text_builder.build_current_amount_text(current_amount),
            )
        except Exception:
            # Side-channel уведомление об остатке акта — best-effort: не должно
            # ломать основной поток, но сбой логируем (раньше молча гасился).
            log.exception(
                "Failed to notify act current amount for chat_id=%s",
                request_chat_id,
            )
