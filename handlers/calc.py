from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

from services.expression_calculator import CalcError, evaluate


def _fmt_decimal_smart(d: Decimal) -> str:
    """
    Красиво формируем число:
    - если целое -> без дробной части
    - иначе -> до 8 знаков, без хвостовых нулей
    """
    s = f"{d.normalize():f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    if "." in s:
        head, tail = s.split(".", 1)
        tail = tail[:8].rstrip("0")
        s = head if not tail else f"{head}.{tail}"
    return s


def _normalize_expr(expr: str) -> str:
    """
    Нормализуем десятичные запятые:
    100.1+100,1 -> 100.1+100.1
    """
    return (expr or "").replace(",", ".")


async def _cmd_calc(message: Message) -> None:
    text = message.text or ""
    raw_expr = text[len("/calc"):].strip() if text.startswith("/calc") else ""
    if not raw_expr:
        await message.answer("Использование: /calc <выражение>\nНапример: /calc (2+3)*100-50%")
        return

    expr = _normalize_expr(raw_expr)

    try:
        result = evaluate(expr)
        sres = _fmt_decimal_smart(result)
        await message.answer(f"{expr} = {sres}")
    except CalcError as e:
        await message.answer(f"Ошибка: {e}")


async def _slash_calc(message: Message) -> None:
    raw = (message.text or "").strip()
    raw_expr = raw[1:].strip()
    if not raw_expr:
        return

    expr = _normalize_expr(raw_expr)

    try:
        result = evaluate(expr)
        sres = _fmt_decimal_smart(result)
        await message.answer(f"{expr} = {sres}")
    except CalcError as e:
        await message.answer(f"Ошибка: {e}")


async def _on_inline(q: InlineQuery) -> None:
    raw_query = (q.query or "").strip()

    if not raw_query:
        hint = "Введите выражение, напр.: (2+3)*10 - 50%"
        await q.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Калькулятор — введите выражение",
                    description=hint,
                    input_message_content=InputTextMessageContent(
                        message_text="Калькулятор: введите выражение после @бота, напр.: <code>(2+3)*10-50%</code>",
                        parse_mode="HTML",
                    ),
                )
            ],
            is_personal=True,
            cache_time=1,
        )
        return

    query = _normalize_expr(raw_query)

    try:
        value = evaluate(query)
    except (CalcError, InvalidOperation) as e:
        await q.answer(
            results=[
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Ошибка",
                    description=str(e),
                    input_message_content=InputTextMessageContent(
                        message_text=f"❌ Ошибка: <code>{e}</code>",
                        parse_mode="HTML",
                    ),
                )
            ],
            is_personal=True,
            cache_time=1,
        )
        return

    value_rounded = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    pretty = f"{value_rounded.normalize():f}"
    if "." in pretty:
        pretty = pretty.rstrip("0").rstrip(".")

    res_full = InlineQueryResultArticle(
        id=str(uuid4()),
        title=f"{pretty}",
        description=(
            f"{query}\n"
            "Нажмите здесь, чтобы поделиться расчетами в этом чате"
        ),
        input_message_content=InputTextMessageContent(
            message_text=f"<code>{query}</code> = <code>{pretty}</code>",
            parse_mode="HTML",
        ),
    )

    await q.answer(
        results=[res_full],
        is_personal=True,
        cache_time=1,
    )


class CalcHandler:
    def __init__(self) -> None:
        self.router = Router()
        self._register()

    def _register(self) -> None:
        self.router.message.register(_cmd_calc, Command("calc"))
        self.router.message.register(_slash_calc, F.text.regexp(r"^/[+\-0-9(]"))
        self.router.inline_query.register(_on_inline, F.query.regexp(r".*"))
