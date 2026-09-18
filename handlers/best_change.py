from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.ports.administration import ManagerRepositoryPort
from domain import DomainStateError, DomainValidationError
from services.best_change import (
    BestChangeAccount,
    BestChangeCorrectionResult,
    BestChangeOperation,
    BestChangePaymentKind,
    BestChangePostingResult,
    BestChangeService,
    CloseBestChangeMonth,
    CorrectBestChangeDeal,
    RecordBestChangeDeal,
    RecordBestChangePayment,
    ReverseBestChangeMonth,
    ReverseBestChangePayment,
    format_month_report,
    format_month_reversal_result,
    format_payment_result,
    format_payment_reversal_result,
    previous_month,
)
from telegram_adapters.auth import manager_or_admin_message_required

_LOCAL_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
_OPERATION_ALIASES = {
    "покупка": BestChangeOperation.PURCHASE,
    "купить": BestChangeOperation.PURCHASE,
    "продажа": BestChangeOperation.SALE,
    "продать": BestChangeOperation.SALE,
}
_PAYMENT_ALIASES = {
    "партнер": BestChangePaymentKind.PARTNER,
    "партнёр": BestChangePaymentKind.PARTNER,
    "partner": BestChangePaymentKind.PARTNER,
    "coindrop": BestChangePaymentKind.COINDROP,
    "коиндроп": BestChangePaymentKind.COINDROP,
}
_USAGE = (
    "Формат: /бест <покупка|продажа> <тюм|члб> "
    "<количество USDT> <курс биржи> <курс клиента>\n"
    "История: /бест история <ДД.ММ.ГГГГ> <покупка|продажа> <тюм|члб> "
    "<количество USDT> <курс биржи> <курс клиента>\n"
    "Справка: /бест отчет [ММ.ГГГГ]\n"
    "Закрытие: /бест закрыть ММ.ГГГГ\n"
    "Выплата: /бест выплата <партнер|coindrop> ММ.ГГГГ <сумма> <ссылка>"
    "\nСторно: /бест сторно <выплата ID|закрытие ММ.ГГГГ> <причина>"
    "\nИсправление: /бест исправить <ID сделки> <покупка|продажа> <город> "
    "<qty> <курс биржи> <курс клиента> <причина>"
)


class BestChangeHandler:
    def __init__(
        self,
        repo: ManagerRepositoryPort,
        *,
        service: BestChangeService,
        chat_id: int,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.service = service
        self.chat_id = chat_id
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    @manager_or_admin_message_required
    async def _cmd_best(self, message: Message) -> None:
        if message.chat.id != self.chat_id:
            await message.answer("Команда /бест работает только в чате BestChange.")
            return
        if message.from_user is None:
            await message.answer("Не удалось определить менеджера.")
            return

        if _is_balances_command(message.text or ""):
            balances = await self.service.balances()
            await message.answer(format_best_change_balances(balances))
            return

        parts = (message.text or "").strip().split()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        if subcommand in {"отчет", "отчёт"}:
            try:
                if len(parts) == 2:
                    message_date = _message_datetime(message).astimezone(
                        _LOCAL_TIMEZONE
                    ).date()
                    period_month = previous_month(message_date)
                elif len(parts) == 3:
                    period_month = parse_best_change_period(parts[2])
                else:
                    raise ValueError("Справка принимает не более одного месяца.")
                report = await self.service.month_report(period_month)
            except (DomainStateError, DomainValidationError, ValueError) as error:
                await message.answer(f"{error}\n\nСправка: /бест отчет [ММ.ГГГГ]")
                return
            await message.answer(format_month_report(report))
            return
        if subcommand == "закрыть":
            try:
                if len(parts) != 3:
                    raise ValueError("Для закрытия месяц нужно указать явно.")
                result = await self.service.close_month(
                    CloseBestChangeMonth(
                        period_month=parse_best_change_period(parts[2]),
                        actor_tg_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                    )
                )
            except (DomainStateError, DomainValidationError, ValueError) as error:
                await message.answer(f"{error}\n\nЗакрытие: /бест закрыть ММ.ГГГГ")
                return
            await message.answer(
                format_month_report(result.report, repeated=result.repeated)
            )
            return
        if subcommand == "выплата":
            try:
                kind, period_month, amount, payment_reference = (
                    parse_best_change_payment_command(message.text or "")
                )
                result = await self.service.record_payment(
                    RecordBestChangePayment(
                        period_month=period_month,
                        kind=kind,
                        amount=amount,
                        payment_reference=payment_reference,
                        actor_tg_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                    )
                )
            except (DomainStateError, DomainValidationError, ValueError) as error:
                await message.answer(
                    f"{error}\n\n"
                    "Выплата: /бест выплата <партнер|coindrop> "
                    "ММ.ГГГГ <сумма> <ссылка>"
                )
                return
            await message.answer(format_payment_result(result))
            return
        if subcommand == "сторно":
            try:
                target, value, reason = parse_best_change_reversal_command(
                    message.text or ""
                )
                if target == "payment":
                    result = await self.service.reverse_payment(
                        ReverseBestChangePayment(
                            payment_id=int(value),
                            actor_tg_user_id=message.from_user.id,
                            chat_id=message.chat.id,
                            message_id=message.message_id,
                            reason=reason,
                        )
                    )
                    response = format_payment_reversal_result(result)
                else:
                    result = await self.service.reverse_month(
                        ReverseBestChangeMonth(
                            period_month=value,
                            actor_tg_user_id=message.from_user.id,
                            chat_id=message.chat.id,
                            message_id=message.message_id,
                            reason=reason,
                        )
                    )
                    response = format_month_reversal_result(result)
            except (DomainStateError, DomainValidationError, ValueError) as error:
                await message.answer(
                    f"{error}\n\n"
                    "Сторно выплаты: /бест сторно выплата <ID> <причина>\n"
                    "Сторно закрытия: /бест сторно закрытие ММ.ГГГГ <причина>"
                )
                return
            await message.answer(response)
            return
        if subcommand == "исправить":
            try:
                (
                    deal_id,
                    operation,
                    city,
                    qty,
                    market_rate,
                    client_rate,
                    reason,
                ) = parse_best_change_correction_command(message.text or "")
                result = await self.service.correct_deal(
                    CorrectBestChangeDeal(
                        deal_id=deal_id,
                        operation=operation,
                        city=city,
                        qty_usdt=qty,
                        market_rate_rub=market_rate,
                        client_rate_rub=client_rate,
                        actor_tg_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        reason=reason,
                    )
                )
            except (DomainStateError, DomainValidationError, ValueError) as error:
                await message.answer(
                    f"{error}\n\n"
                    "Исправление: /бест исправить <ID сделки> "
                    "<покупка|продажа> <тюм|члб> <qty> "
                    "<курс биржи> <курс клиента> <причина>"
                )
                return
            await message.answer(format_best_change_correction_result(result))
            return

        if subcommand == "история":
            try:
                (
                    deal_at,
                    operation,
                    city,
                    qty,
                    market_rate,
                    client_rate,
                ) = parse_best_change_history_command(message.text or "")
                result = await self.service.record(
                    RecordBestChangeDeal(
                        operation=operation,
                        city=city,
                        qty_usdt=qty,
                        market_rate_rub=market_rate,
                        client_rate_rub=client_rate,
                        actor_tg_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        deal_at=deal_at,
                        comment="Исторический ввод BestChange",
                    )
                )
            except (DomainStateError, DomainValidationError, ValueError) as error:
                await message.answer(f"{error}\n\n{_USAGE}")
                return
            await message.answer(
                f"📅 Дата исторической сделки: {deal_at:%d.%m.%Y}\n"
                f"{format_best_change_result(result)}"
            )
            return

        try:
            operation, city, qty, market_rate, client_rate = parse_best_change_command(
                message.text or ""
            )
            result = await self.service.record(
                RecordBestChangeDeal(
                    operation=operation,
                    city=city,
                    qty_usdt=qty,
                    market_rate_rub=market_rate,
                    client_rate_rub=client_rate,
                    actor_tg_user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    deal_at=_message_datetime(message).astimezone(_LOCAL_TIMEZONE).date(),
                )
            )
        except (DomainStateError, DomainValidationError, ValueError) as error:
            await message.answer(f"{error}\n\n{_USAGE}")
            return

        await message.answer(format_best_change_result(result))

    @manager_or_admin_message_required
    async def _cmd_wallet(self, message: Message) -> None:
        balances = await self.service.balances()
        await message.answer(format_best_change_balances(balances))

    @manager_or_admin_message_required
    async def _cmd_nonzero(self, message: Message) -> None:
        balances = await self.service.balances()
        await message.answer(format_best_change_balances(balances, only_nonzero=True))

    def _register(self) -> None:
        self.router.message.register(self._cmd_best, Command("бест"))
        self.router.message.register(
            self._cmd_wallet,
            Command("кошелек"),
            F.chat.id == self.chat_id,
        )
        self.router.message.register(
            self._cmd_nonzero,
            Command("дай"),
            F.chat.id == self.chat_id,
        )


def parse_best_change_command(
    text: str,
) -> tuple[BestChangeOperation, str, Decimal, Decimal, Decimal]:
    parts = text.strip().split()
    if len(parts) != 6 or not parts[0].lower().split("@", 1)[0] == "/бест":
        raise ValueError(_USAGE)
    return _parse_best_change_deal_values(parts[1:])


def parse_best_change_history_command(
    text: str,
) -> tuple[date, BestChangeOperation, str, Decimal, Decimal, Decimal]:
    parts = text.strip().split()
    if (
        len(parts) != 8
        or parts[0].lower().split("@", 1)[0] != "/бест"
        or parts[1].lower() != "история"
    ):
        raise ValueError(_USAGE)
    deal_at = parse_best_change_date(parts[2])
    return (deal_at, *_parse_best_change_deal_values(parts[3:]))


def parse_best_change_date(value: str) -> date:
    raw = value.strip()
    for date_format in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    raise ValueError("Дата должна быть в формате ДД.ММ.ГГГГ.")


def _parse_best_change_deal_values(
    parts: list[str],
) -> tuple[BestChangeOperation, str, Decimal, Decimal, Decimal]:
    if len(parts) != 5:
        raise ValueError(_USAGE)
    operation = _OPERATION_ALIASES.get(parts[0].lower())
    if operation is None:
        raise ValueError("Направление должно быть «покупка» или «продажа».")
    try:
        qty, market_rate, client_rate = (
            Decimal(value.replace(",", ".")) for value in parts[2:]
        )
    except InvalidOperation:
        raise ValueError("Количество и курсы должны быть числами.") from None
    return operation, parts[1], qty, market_rate, client_rate


def parse_best_change_period(value: str) -> date:
    raw = value.strip()
    for date_format in ("%m.%Y", "%Y-%m"):
        try:
            return datetime.strptime(raw, date_format).date().replace(day=1)
        except ValueError:
            continue
    raise ValueError("Месяц должен быть в формате ММ.ГГГГ.")


def parse_best_change_payment_command(
    text: str,
) -> tuple[BestChangePaymentKind, date, Decimal, str]:
    parts = text.strip().split()
    if (
        len(parts) < 6
        or parts[0].lower().split("@", 1)[0] != "/бест"
        or parts[1].lower() != "выплата"
    ):
        raise ValueError(
            "Формат: /бест выплата <партнер|coindrop> "
            "ММ.ГГГГ <сумма> <ссылка>"
        )
    kind = _PAYMENT_ALIASES.get(parts[2].lower())
    if kind is None:
        raise ValueError("Получатель должен быть «партнер» или «coindrop».")
    try:
        amount = Decimal(parts[4].replace(",", "."))
    except InvalidOperation:
        raise ValueError("Сумма выплаты должна быть числом.") from None
    return kind, parse_best_change_period(parts[3]), amount, " ".join(parts[5:])


def parse_best_change_reversal_command(text: str) -> tuple[str, int | date, str]:
    parts = text.strip().split()
    if (
        len(parts) < 5
        or parts[0].lower().split("@", 1)[0] != "/бест"
        or parts[1].lower() != "сторно"
    ):
        raise ValueError(
            "Формат: /бест сторно <выплата ID|закрытие ММ.ГГГГ> <причина>"
        )
    target = parts[2].lower()
    reason = " ".join(parts[4:])
    if target in {"выплата", "платеж", "платёж"}:
        try:
            payment_id = int(parts[3])
        except ValueError:
            raise ValueError("ID выплаты должен быть целым числом.") from None
        return "payment", payment_id, reason
    if target == "закрытие":
        return "month", parse_best_change_period(parts[3]), reason
    raise ValueError("Сторнировать можно выплату или закрытие месяца.")


def parse_best_change_correction_command(
    text: str,
) -> tuple[int, BestChangeOperation, str, Decimal, Decimal, Decimal, str]:
    parts = text.strip().split()
    if (
        len(parts) < 9
        or parts[0].lower().split("@", 1)[0] != "/бест"
        or parts[1].lower() != "исправить"
    ):
        raise ValueError(
            "Формат: /бест исправить <ID сделки> <покупка|продажа> "
            "<тюм|члб> <qty> <курс биржи> <курс клиента> <причина>"
        )
    try:
        deal_id = int(parts[2])
    except ValueError:
        raise ValueError("ID сделки должен быть целым числом.") from None
    operation = _OPERATION_ALIASES.get(parts[3].lower())
    if operation is None:
        raise ValueError("Направление должно быть «покупка» или «продажа».")
    try:
        qty, market_rate, client_rate = (
            Decimal(value.replace(",", ".")) for value in parts[5:8]
        )
    except InvalidOperation:
        raise ValueError("Количество и курсы должны быть числами.") from None
    return (
        deal_id,
        operation,
        parts[4],
        qty,
        market_rate,
        client_rate,
        " ".join(parts[8:]),
    )


def format_best_change_result(result: BestChangePostingResult) -> str:
    calculation = result.calculation
    heading = "♻️ Сделка уже была проведена" if result.repeated else "✅ Сделка проведена"
    warning = "\n⚠️ Сделка убыточная, комиссия CoinDrop не начислена." if calculation.is_loss else ""
    operation = "Покупка" if calculation.operation is BestChangeOperation.PURCHASE else "Продажа"
    return (
        f"{heading} · #{result.deal_id}{warning}\n\n"
        f"{operation}, {calculation.city.upper()} · {calculation.qty_usdt} USDT\n"
        f"Курс биржи: {calculation.market_rate_rub}\n"
        f"Курс клиента: {calculation.client_rate_rub}\n"
        f"Валовый спред: {calculation.gross_spread_rub} RUB\n"
        f"Фонд прибыли: {calculation.profit_pool_rub} RUB\n"
        f"Доля партнёра: {calculation.partner_share_rub} RUB\n"
        f"Прибыль SkyEx: {calculation.skyex_profit_rub} RUB\n"
        f"Комиссия CoinDrop: {calculation.platform_fee_usdt} USDT\n\n"
        f"Баланс этого счёта: {_format_rub(result.profit_balance_rub)} RUB\n"
        f"Остаток CoinDrop: {_format_usdt(result.platform_fee_balance_usdt)} USDT"
    )


def format_best_change_correction_result(
    result: BestChangeCorrectionResult,
) -> str:
    heading = (
        "♻️ Исправление уже было проведено"
        if result.repeated
        else "✅ Сделка исправлена"
    )
    replacement = format_best_change_result(result.replacement)
    return (
        f"{heading}\n"
        f"Исходная сделка: #{result.original_deal_id}\n"
        f"Новая сделка: #{result.replacement.deal_id}\n\n"
        f"{replacement}"
    )


def format_best_change_balances(
    balances: dict[BestChangeAccount, Decimal],
    *,
    only_nonzero: bool = False,
) -> str:
    account_rows = (
        ("Покупки Тюм", BestChangeAccount.PURCHASE_TYM, "RUB", _format_rub),
        ("Продажи Тюм", BestChangeAccount.SALE_TYM, "RUB", _format_rub),
        ("Покупки Члб", BestChangeAccount.PURCHASE_CHLB, "RUB", _format_rub),
        ("Продажи Члб", BestChangeAccount.SALE_CHLB, "RUB", _format_rub),
        ("Остаток CoinDrop", BestChangeAccount.PLATFORM_FEE, "USDT", _format_usdt),
    )
    rows = [
        f"{label}: {formatter(balances[account])} {currency}"
        for label, account, currency, formatter in account_rows
        if not only_nonzero or balances[account] != 0
    ]
    if not rows:
        return "Все счета BestChange нулевые. Посмотреть всё: /кошелек"
    return "📊 Счета BestChange\n\n" + "\n".join(rows)


def _is_balances_command(text: str) -> bool:
    parts = text.strip().lower().split()
    return len(parts) == 2 and parts[0].split("@", 1)[0] == "/бест" and parts[1] == "итог"


def _format_rub(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_usdt(value: Decimal) -> str:
    return f"{value:.6f}"


def _message_datetime(message: Message) -> datetime:
    value = message.date
    if value.tzinfo is None:
        return value.replace(tzinfo=_LOCAL_TIMEZONE)
    return value
