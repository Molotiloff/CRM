from __future__ import annotations

from collections.abc import Callable
from datetime import date

from services.messaging import MessengerPort

from .models import (
    BestChangeMonthReport,
    BestChangeMonthReversalResult,
    BestChangePaymentKind,
    BestChangePaymentResult,
    BestChangePaymentReversalResult,
)
from .service import BestChangeService


def previous_month(value: date) -> date:
    current_month = value.replace(day=1)
    previous_day = date.fromordinal(current_month.toordinal() - 1)
    return previous_day.replace(day=1)


def format_month_report(
    report: BestChangeMonthReport,
    *,
    preliminary: bool = False,
    repeated: bool = False,
) -> str:
    if report.closure_id is not None:
        heading = f"✅ BestChange закрыт · {report.period_month:%m.%Y} · #{report.closure_id}"
        if repeated:
            heading = f"♻️ Период уже закрыт · {report.period_month:%m.%Y} · #{report.closure_id}"
    elif preliminary:
        heading = f"📋 Предварительная справка BestChange · {report.period_month:%m.%Y}"
    else:
        heading = f"📋 Справка BestChange · {report.period_month:%m.%Y}"
    deal_ids = ", ".join(f"#{deal_id}" for deal_id in report.deal_ids) or "нет"
    closure_details = ""
    if report.closure_id is not None:
        actor = report.closed_by_tg_user_id or "неизвестен"
        closed_at = (
            report.closed_at.strftime("%d.%m.%Y %H:%M")
            if report.closed_at is not None
            else "неизвестно"
        )
        closure_details = f"\nЗакрыл: {actor} · {closed_at}"
    if report.platform_fee_delta_usdt == 0:
        platform_status = "оплачено"
    elif report.platform_fee_paid_usdt == 0:
        platform_status = "не оплачено"
    else:
        platform_status = "оплачено частично"
    return (
        f"{heading}\n\n"
        f"Покупки Тюм: {report.purchase_tym_rub:.2f} RUB\n"
        f"Продажи Тюм: {report.sale_tym_rub:.2f} RUB\n"
        f"Покупки Члб: {report.purchase_chlb_rub:.2f} RUB\n"
        f"Продажи Члб: {report.sale_chlb_rub:.2f} RUB\n\n"
        f"Общий фонд: {report.profit_pool_rub:.2f} RUB\n"
        f"Доля партнёра: {report.partner_share_rub:.2f} RUB\n"
        f"Выплачено партнёру: {report.partner_paid_rub:.2f} RUB\n"
        f"Остаток партнёру: {report.partner_delta_rub:.2f} RUB\n"
        f"Прибыль SkyEx: {report.skyex_profit_rub:.2f} RUB\n\n"
        f"Начислено CoinDrop: {report.platform_fee_accrued_usdt:.6f} USDT\n"
        f"Оплачено CoinDrop: {report.platform_fee_paid_usdt:.6f} USDT\n"
        f"Дельта периода: {report.platform_fee_delta_usdt:.6f} USDT\n"
        f"Статус CoinDrop: {platform_status}\n"
        f"Общий остаток CoinDrop: {report.platform_fee_outstanding_usdt:.6f} USDT\n\n"
        f"Сделок: {report.deal_count}\n"
        f"CRM: {deal_ids}{closure_details}"
    )


def format_payment_result(result: BestChangePaymentResult) -> str:
    heading = (
        "♻️ Выплата уже была проведена"
        if result.repeated
        else "✅ Выплата проведена"
    )
    recipient = (
        "Партнёр" if result.kind is BestChangePaymentKind.PARTNER else "CoinDrop"
    )
    precision = 2 if result.currency == "RUB" else 6
    return (
        f"{heading} · #{result.payment_id}\n\n"
        f"Получатель: {recipient}\n"
        f"Период: {result.report.period_month:%m.%Y}\n"
        f"Сумма: {result.amount:.{precision}f} {result.currency}\n"
        f"Ссылка: {result.payment_reference}\n\n"
        f"{format_month_report(result.report)}"
    )


def format_payment_reversal_result(result: BestChangePaymentReversalResult) -> str:
    heading = "♻️ Выплата уже сторнирована" if result.repeated else "✅ Выплата сторнирована"
    return (
        f"{heading}\n"
        f"Исходная выплата: #{result.original_payment_id}\n"
        f"Сторно: #{result.reversal_payment_id}\n\n"
        f"{format_month_report(result.report)}"
    )


def format_month_reversal_result(result: BestChangeMonthReversalResult) -> str:
    heading = "♻️ Закрытие уже сторнировано" if result.repeated else "✅ Закрытие сторнировано"
    return (
        f"{heading} · {result.period_month:%m.%Y}\n"
        f"Исходное закрытие: #{result.original_closure_id}\n"
        f"Сторно: #{result.reversal_closure_id}\n\n"
        "Четыре RUB-счёта восстановлены. Месяц можно закрыть заново."
    )


class BestChangeMonthlyReportPublisher:
    def __init__(
        self,
        *,
        service: BestChangeService,
        messenger: MessengerPort,
        chat_id: int,
        today_factory: Callable[[], date] = date.today,
    ) -> None:
        self._service = service
        self._messenger = messenger
        self._chat_id = chat_id
        self._today = today_factory

    async def publish_previous_month(self) -> None:
        report = await self._service.month_report(previous_month(self._today()))
        await self._messenger.send(
            chat_id=self._chat_id,
            text=format_month_report(report, preliminary=True),
        )
