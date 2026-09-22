from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.types import InputMediaPhoto, Message

from db_asyncpg.ports.workflows import ClientTransferRepositoryPort
from services.number_formatting import format_amount_core, format_amount_with_sign
from telegram_adapters.city_cash_media_store import CityCashMediaStore
from telegram_adapters.message_context import get_chat_name

log = logging.getLogger("city_cash_transfer")


@dataclass(frozen=True, slots=True)
class CityTransferResult:
    ok: bool
    error: str | None = None
    target_chat_id: int | None = None
    target_client_id: int | None = None
    pretty_delta: str | None = None
    pretty_balance: str | None = None


def _pick_photo_file_id(message: Message) -> str | None:
    if not message.photo:
        return None
    return message.photo[-1].file_id


def _pick_photo_file_ids(messages: list[Message]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        if not msg.photo:
            continue
        out.append(msg.photo[-1].file_id)
    return out


async def _safe_send_with_migration(
    *,
    repo: ClientTransferRepositoryPort,
    bot: Bot,
    target_chat_id: int,
    target_client_id: int,
    send_coro_factory: Callable[[int], Awaitable[object]],
) -> int:
    """
    Пытается отправить в target_chat_id.
    Если чат мигрировал group->supergroup, обновляет clients.chat_id и ретраит.
    Возвращает актуальный chat_id (возможно новый).
    """
    try:
        await send_coro_factory(target_chat_id)
        return target_chat_id
    except TelegramMigrateToChat as e:
        new_chat_id = int(getattr(e, "migrate_to_chat_id", 0) or 0)
        if not new_chat_id:
            raise

        log.warning(
            "CHAT MIGRATION detected: old_chat_id=%s -> new_chat_id=%s (client_id=%s)",
            target_chat_id, new_chat_id, target_client_id,
        )

        # 1) обновляем в БД
        try:
            await repo.update_client_chat_id(client_id=target_client_id, new_chat_id=new_chat_id)
        except Exception:
            log.exception("FAILED updating clients.chat_id client_id=%s new_chat_id=%s", target_client_id, new_chat_id)
            # даже если БД не обновилась — пробуем отправить по новому chat_id

        # 2) ретраим отправку в новый чат
        await send_coro_factory(new_chat_id)
        return new_chat_id


async def send_cash_settlement_evidence_to_client(
    *,
    repo: ClientTransferRepositoryPort,
    bot: Bot,
    evidence_messages: list[Message],
    target_chat_id: int,
    target_client_id: int,
) -> int:
    """Send attached cash-settlement photos without changing client balances."""
    photo_file_ids = _pick_photo_file_ids(evidence_messages)
    if not photo_file_ids:
        return target_chat_id

    async def _send_evidence(chat_id: int):
        if len(photo_file_ids) == 1:
            return await bot.send_photo(chat_id=chat_id, photo=photo_file_ids[0])
        return await bot.send_media_group(
            chat_id=chat_id,
            media=[InputMediaPhoto(media=file_id) for file_id in photo_file_ids],
        )

    return await _safe_send_with_migration(
        repo=repo,
        bot=bot,
        target_chat_id=target_chat_id,
        target_client_id=target_client_id,
        send_coro_factory=_send_evidence,
    )


async def city_cash_transfer_to_client(
    *,
    repo: ClientTransferRepositoryPort,
    bot: Bot,
    src_message: Message,
    media_store: CityCashMediaStore | None,
    currency_code: str,
    amount_signed: Decimal,
    amount_expr: str,
    client_name_exact: str,
    extra_comment: str = "",
) -> CityTransferResult:
    # 1) найти клиента
    found = await repo.find_client_by_name_exact(client_name_exact)
    if not found:
        return CityTransferResult(
            ok=False,
            error=(
                "⚠️ Не нашёл клиента по имени.\n"
                "Проверьте, что имя вставлено ТОЧНО как в строке 'Клиент:' (включая пробелы и символы)."
            ),
        )

    target_chat_id = int(found["chat_id"])
    target_client_id = int(found["id"])
    code = (currency_code or "").strip().upper()

    # 2) проверить счёт клиента и точность
    target_accounts = await repo.snapshot_wallet(target_client_id)
    target_acc = next((r for r in target_accounts if str(r["currency_code"]).upper() == code), None)
    if not target_acc:
        return CityTransferResult(
            ok=False,
            error=(
                f"⚠️ У клиента нет счёта {code}.\n"
                f"Нужно добавить в клиентском чате: /добавь {code} [точность]"
            ),
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    target_prec = int(target_acc["precision"]) if target_acc.get("precision") is not None else 2
    q = Decimal(10) ** -target_prec
    delta_abs = amount_signed.copy_abs().quantize(q, rounding=ROUND_HALF_UP)

    if delta_abs == 0:
        return CityTransferResult(
            ok=False,
            error=f"⚠️ Сумма слишком мала для точности клиента ({target_prec}).",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    # 3) применить операцию в кошельке клиента
    idem2 = f"city_transfer:{src_message.chat.id}:{src_message.message_id}:to:{target_chat_id}"

    city_tag = get_chat_name(src_message)
    comment = amount_expr
    if extra_comment:
        comment = f"{amount_expr} | {extra_comment}"
    comment = f"{comment} | касса: {city_tag}"

    try:
        if amount_signed > 0:
            await repo.deposit(
                client_id=target_client_id,
                currency_code=code,
                amount=delta_abs,
                comment=comment,
                source="city_transfer",
                idempotency_key=idem2,
            )
            pretty_delta = format_amount_with_sign(delta_abs, target_prec, sign="+")
        else:
            await repo.withdraw(
                client_id=target_client_id,
                currency_code=code,
                amount=delta_abs,
                comment=comment,
                source="city_transfer",
                idempotency_key=idem2,
            )
            pretty_delta = format_amount_with_sign(delta_abs, target_prec, sign="-")
    except Exception:
        log.exception("FAILED repo operation for client_id=%s code=%s", target_client_id, code)
        return CityTransferResult(
            ok=False,
            error="⚠️ Не удалось применить операцию в кошельке клиента.",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    # 4) отправить фото/квитанцию
    photo_file_ids: list[str] = []
    if src_message.media_group_id and media_store is not None:
        previous_size = -1
        stable_passes = 0
        for _ in range(5):
            await asyncio.sleep(0.25)
            current_size = media_store.group_size(
                chat_id=src_message.chat.id,
                media_group_id=src_message.media_group_id,
            )
            if current_size > 0 and current_size == previous_size:
                stable_passes += 1
            else:
                stable_passes = 0
            previous_size = current_size
            if current_size > 1 and stable_passes >= 1:
                break

        group_messages = media_store.pop_group(
            chat_id=src_message.chat.id,
            media_group_id=src_message.media_group_id,
        )
        photo_file_ids = _pick_photo_file_ids(group_messages)

    if not photo_file_ids:
        photo_file_id = _pick_photo_file_id(src_message)
        if photo_file_id:
            photo_file_ids = [photo_file_id]

    caption = f"/{code.lower()} {amount_expr}".strip()
    if extra_comment:
        caption += f" {extra_comment}"

    async def _send_receipt(chat_id: int):
        if len(photo_file_ids) > 1:
            media = [
                InputMediaPhoto(media=file_id, caption=caption if idx == 0 else None)
                for idx, file_id in enumerate(photo_file_ids)
            ]
            return await bot.send_media_group(chat_id=chat_id, media=media)
        if len(photo_file_ids) == 1:
            return await bot.send_photo(chat_id=chat_id, photo=photo_file_ids[0], caption=caption)
        else:
            return await bot.send_message(chat_id=chat_id, text=caption)

    try:
        target_chat_id = await _safe_send_with_migration(
            repo=repo,
            bot=bot,
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
            send_coro_factory=_send_receipt,
        )
    except Exception:
        log.exception("FAILED sending receipt to client chat. client_id=%s chat_id=%s", target_client_id,
                      target_chat_id)
        return CityTransferResult(
            ok=False,
            error="⚠️ Не удалось отправить квитанцию/фото в чат клиента (проверьте доступ бота).",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
        )

    # 5) отправить баланс клиента после операции (тоже через safe-migration)
    try:
        target_accounts2 = await repo.snapshot_wallet(target_client_id)
        target_acc2 = next((r for r in target_accounts2 if str(r["currency_code"]).upper() == code), None)
        target_bal = Decimal(str(target_acc2["balance"])) if target_acc2 else Decimal("0")
        target_prec2 = int(target_acc2["precision"]) if target_acc2 and target_acc2.get("precision") is not None else (
            target_prec)
        pretty_bal = format_amount_core(target_bal, target_prec2)

        async def _send_balance(chat_id: int):
            text = f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {code.lower()}"
            return await bot.send_message(chat_id=chat_id, text=text)

        target_chat_id = await _safe_send_with_migration(
            repo=repo,
            bot=bot,
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
            send_coro_factory=_send_balance,
        )

    except Exception:
        log.exception("FAILED sending balance to client chat. client_id=%s chat_id=%s", target_client_id,
                      target_chat_id)
        # баланс не критичен — считаем успехом, но вернём предупреждение
        return CityTransferResult(
            ok=True,
            error="⚠️ Операцию продублировал, но не смог отправить баланс (проверьте права бота).",
            target_chat_id=target_chat_id,
            target_client_id=target_client_id,
            pretty_delta=pretty_delta,
            pretty_balance=None,
        )

    return CityTransferResult(
        ok=True,
        target_chat_id=target_chat_id,
        target_client_id=target_client_id,
        pretty_delta=pretty_delta,
        pretty_balance=pretty_bal,
    )
