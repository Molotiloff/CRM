from __future__ import annotations

from collections.abc import Mapping

from db_asyncpg.ports.workflows import CashRequestContextRepositoryPort
from observability import NULL_METRICS, MetricsRecorder
from services.cash_requests.calculator import CashRequestCalculator
from services.cash_requests.card_parser import CashCardParser
from services.cash_requests.card_presenter import CashCardPresenter
from services.cash_requests.create_cash_request import CreateCashRequest
from services.cash_requests.edit_cash_request import EditCashRequest
from services.cash_requests.parsing import ParsedRequest, parse_dep_wd, parse_fx
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.cash_requests.schedule_coordinator import CashScheduleCoordinator
from services.crm.telegram_deal_registrar import TelegramDealRegistrarPort


class CashRequestService:
    def __init__(
        self,
        *,
        repo: CashRequestContextRepositoryPort,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
        cmd_map: Mapping[str, tuple[str, str]],
        fx_cmd_map: Mapping[str, tuple[str, str, str]],
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
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
        self.cmd_map = dict(cmd_map)
        self.fx_cmd_map = dict(fx_cmd_map)
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self.create_cash_request = CreateCashRequest(
            repo=repo,
            router_service=router_service,
            schedule_service=schedule_service,
            deal_registrar=deal_registrar,
            calculator=calculator,
            card_presenter=card_presenter,
            card_parser=card_parser,
            schedule_coordinator=schedule_coordinator,
            metrics=metrics,
        )
        self.edit_cash_request = EditCashRequest(
            repo=repo,
            router_service=router_service,
            schedule_service=schedule_service,
            calculator=calculator,
            card_presenter=card_presenter,
            card_parser=card_parser,
            schedule_coordinator=schedule_coordinator,
            metrics=metrics,
        )

    @property
    def supported_commands(self) -> tuple[str, ...]:
        return tuple(set(self.cmd_map.keys()) | set(self.fx_cmd_map.keys()))

    def help_text(self) -> str:
        cities = (
            ", ".join(sorted(self.router_service.city_keys))
            if self.router_service.city_keys
            else "—"
        )
        return (
            "Форматы:\n"
            "• /депр [город] <сумма/expr> [Принимает] [Выдает] [! комментарий]\n"
            "• /выдр [город] <сумма/expr> [Выдает] [Принимает] [! комментарий]\n"
            "• /првд [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /пдвр [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /прве [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n\n"
            f"Города: {cities}\n"
            f"Если город не указан — по умолчанию: {self.router_service.default_city}\n\n"
            "Расчет в городской кассе:\n"
            "• сначала нажмите «Готово к расчету» в карточке;\n"
            "• затем укажите номер заявки в кассовой команде, например "
            "/usd -125 Б-123456.\n\n"
            "Редактирование:\n"
            "• ответьте командой на карточку БОТА — можно менять сумму, город, контакты, комментарий;\n"
            "• тип и валюты менять нельзя."
        )

    def parse_command(self, raw_text: str) -> ParsedRequest | None:
        parsed: ParsedRequest | None = parse_fx(
            raw_text,
            fx_cmd_map=self.fx_cmd_map,
            city_keys=self.router_service.city_keys,
            default_city=self.router_service.default_city,
        )
        if not parsed:
            parsed = parse_dep_wd(
                raw_text,
                cmd_map=self.cmd_map,
                city_keys=self.router_service.city_keys,
                default_city=self.router_service.default_city,
            )
        return parsed
