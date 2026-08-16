from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from services.client_balances.daily_report_service import DailyBalancesReportService
from services.messaging import MessengerPort

SCHEDULED_RUB_DEBT_THRESHOLD = Decimal("-1000")
SCHEDULED_USDT_DEBT_THRESHOLD = Decimal("-1")
EXCLUDED_SCHEDULED_GROUP = "Балансы"


@dataclass(slots=True, frozen=True)
class ScheduledBalanceSection:
    code: str
    threshold: Decimal


class ScheduledBalancesReportService:
    def __init__(
        self,
        *,
        report_service: DailyBalancesReportService,
        messenger: MessengerPort,
        admin_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.report_service = report_service
        self.messenger = messenger
        self.admin_chat_ids = set(admin_chat_ids or [])

    async def send_negative_balances(
        self,
        *,
        currencies: Iterable[str] = ("RUB", "USDT"),
    ) -> None:
        sections = self._sections(currencies)
        for chat_id in self.admin_chat_ids:
            await self.messenger.send(
                chat_id=chat_id,
                text="📊 <b>Ежедневный отчёт по балансам</b>",
            )

            has_any = False
            for section in sections:
                chunks = await self.report_service.build_report(
                    code_filter=section.code,
                    sign_filter="-",
                    min_negative_balance=section.threshold,
                    excluded_client_group=EXCLUDED_SCHEDULED_GROUP,
                )
                if self._is_empty(chunks):
                    continue
                has_any = True
                for chunk in chunks:
                    await self.messenger.send(chat_id=chat_id, text=chunk)

            if not has_any:
                await self.messenger.send(
                    chat_id=chat_id,
                    text="Подходящих балансов по выбранным условиям нет.",
                )

    @staticmethod
    def _sections(currencies: Iterable[str]) -> list[ScheduledBalanceSection]:
        thresholds = {
            "RUB": SCHEDULED_RUB_DEBT_THRESHOLD,
            "USDT": SCHEDULED_USDT_DEBT_THRESHOLD,
        }
        return [
            ScheduledBalanceSection(code=code, threshold=thresholds[code])
            for raw_code in currencies
            if (code := raw_code.strip().upper()) in thresholds
        ]

    @staticmethod
    def _is_empty(chunks: list[str]) -> bool:
        if not chunks:
            return True
        return len(chunks) == 1 and chunks[0].strip().lower().startswith("нет клиентов")
