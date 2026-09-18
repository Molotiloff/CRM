from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from db_asyncpg.ports.administration import ManagerRepositoryPort
from db_asyncpg.ports.clients import ClientRepositoryPort
from services.message_archive.export_service import GeneratedExport, MessageExportService
from services.message_archive.ports import MessageArchiveRepositoryPort

log = logging.getLogger("message_archive.handler")


class MessageArchiveHandler:
    CALLBACK_PREFIX = "message_archive:chat:"

    def __init__(
        self,
        *,
        bot: Bot,
        repo: MessageArchiveRepositoryPort,
        client_repo: ClientRepositoryPort,
        manager_repo: ManagerRepositoryPort,
        export_service: MessageExportService,
        admin_chat_id: int,
        admin_user_ids: set[int] | None = None,
        export_workers: int = 1,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.client_repo = client_repo
        self.manager_repo = manager_repo
        self.export_service = export_service
        self.admin_chat_id = int(admin_chat_id)
        self.admin_user_ids = set(admin_user_ids or set())
        self._semaphore = asyncio.Semaphore(max(1, export_workers))
        self._max_pending_exports = max(4, export_workers * 4)
        self._tasks: set[asyncio.Task[None]] = set()
        self.router = Router(name="message_archive")
        self._register()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _authorized_message(self, message: Message) -> bool:
        if message.chat.id != self.admin_chat_id:
            await message.answer("⛔ Выгрузка истории доступна только в админском чате.")
            return False
        if not message.from_user:
            await message.answer("⛔ Не удалось определить пользователя.")
            return False
        if message.from_user.id in self.admin_user_ids:
            return True
        if await self.manager_repo.is_manager(message.from_user.id):
            return True
        await message.answer("⛔ Выгрузка истории доступна только менеджерам.")
        return False

    async def _authorized_callback(self, callback: CallbackQuery) -> bool:
        if not callback.message or callback.message.chat.id != self.admin_chat_id:
            await callback.answer("Недоступно вне админского чата.", show_alert=True)
            return False
        if callback.from_user.id in self.admin_user_ids:
            return True
        if await self.manager_repo.is_manager(callback.from_user.id):
            return True
        await callback.answer("Доступно только менеджерам.", show_alert=True)
        return False

    async def _command(self, message: Message) -> None:
        if not await self._authorized_message(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        query = parts[1].strip() if len(parts) > 1 else ""
        if not query:
            await message.answer("Использование: /сообщение &lt;название чата&gt;", parse_mode="HTML")
            return
        user = message.from_user
        if user is None:
            return
        matches = await self.repo.search_archive_chats(query, limit=20)
        if not matches:
            client = await self.client_repo.find_client_by_name_exact(query)
            if client:
                await message.answer(
                    f"Клиент «{client['name']}» найден, chat_id={client['chat_id']}, "
                    "но сохранённых сообщений этого чата в архиве пока нет. "
                    "Архив начнёт отображаться после получения ботом нового сообщения "
                    "или после импорта старой истории."
                )
            else:
                await message.answer(f"Чат «{query}» в архиве и списке клиентов не найден.")
            return
        if len(matches) == 1:
            started = self._start_export(
                archive_chat_id=int(matches[0]["id"]),
                requested_by_user_id=user.id,
                requested_in_chat_id=message.chat.id,
            )
            if not started:
                await message.answer("⏳ Очередь экспортов заполнена. Попробуйте немного позже.")
                return
            await message.answer(f"⏳ Формирую архив чата «{matches[0]['current_title']}»…")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self._chat_button_text(row),
                        callback_data=f"{self.CALLBACK_PREFIX}{int(row['id'])}",
                    )
                ]
                for row in matches
            ]
        )
        await message.answer("Найдено несколько чатов. Выберите нужный:", reply_markup=keyboard)

    async def _choose_chat(self, callback: CallbackQuery) -> None:
        if not await self._authorized_callback(callback):
            return
        callback_message = callback.message
        if not isinstance(callback_message, Message):
            await callback.answer("Сообщение больше недоступно.", show_alert=True)
            return
        try:
            archive_chat_id = int((callback.data or "").removeprefix(self.CALLBACK_PREFIX))
        except ValueError:
            await callback.answer("Некорректный идентификатор.", show_alert=True)
            return
        chat = await self.repo.get_archive_chat(archive_chat_id)
        if not chat:
            await callback.answer("Чат больше не существует.", show_alert=True)
            return
        started = self._start_export(
            archive_chat_id=archive_chat_id,
            requested_by_user_id=callback.from_user.id,
            requested_in_chat_id=callback_message.chat.id,
        )
        if not started:
            await callback.answer("Очередь экспортов заполнена.", show_alert=True)
            return
        await callback_message.edit_reply_markup(reply_markup=None)
        await callback_message.answer(f"⏳ Формирую архив чата «{chat['current_title']}»…")
        await callback.answer("Экспорт запущен")

    def _start_export(
        self, *, archive_chat_id: int, requested_by_user_id: int, requested_in_chat_id: int
    ) -> bool:
        if len(self._tasks) >= self._max_pending_exports:
            return False
        task = asyncio.create_task(
            self._run_export(
                archive_chat_id=archive_chat_id,
                requested_by_user_id=requested_by_user_id,
                requested_in_chat_id=requested_in_chat_id,
            ),
            name=f"message_export:{archive_chat_id}:{requested_by_user_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _run_export(
        self, *, archive_chat_id: int, requested_by_user_id: int, requested_in_chat_id: int
    ) -> None:
        export_id: int | None = None
        generated: GeneratedExport | None = None
        sent_ids: list[int] = []
        try:
            async with self._semaphore:
                export_id = await self.repo.create_archive_export(
                    chat_id=archive_chat_id,
                    requested_by_user_id=requested_by_user_id,
                    requested_in_chat_id=requested_in_chat_id,
                )
                generated = await self.export_service.generate(chat_id=archive_chat_id)
                total_messages = sum(part.message_count for part in generated.parts)
                total_size = sum(part.size for part in generated.parts)
                for index, part in enumerate(generated.parts, start=1):
                    sent = await self.bot.send_document(
                        chat_id=requested_in_chat_id,
                        document=FSInputFile(Path(part.path)),
                        caption=(
                            f"Архив сообщений: часть {index}/{len(generated.parts)}, "
                            f"сообщений: {part.message_count}"
                        ),
                    )
                    sent_ids.append(sent.message_id)
                await self._safe_finish_export(
                    export_id=export_id,
                    status="completed",
                    message_count=total_messages,
                    part_count=len(generated.parts),
                    total_size=total_size,
                    telegram_message_ids=sent_ids,
                )
        except asyncio.CancelledError:
            if export_id is not None:
                await self._safe_finish_export(
                    export_id=export_id, status="cancelled", error="Application shutdown"
                )
            raise
        except Exception as exc:
            log.exception("Message archive export failed chat_id=%s", archive_chat_id)
            if export_id is not None:
                await self._safe_finish_export(
                    export_id=export_id,
                    status="failed",
                    telegram_message_ids=sent_ids,
                    error=f"{type(exc).__name__}: {exc}",
                )
            try:
                await self.bot.send_message(
                    chat_id=requested_in_chat_id,
                    text="❌ Не удалось сформировать архив сообщений. Ошибка записана в журнал.",
                )
            except Exception:
                log.exception("Failed to notify manager about message export failure")
        finally:
            if generated:
                generated.cleanup()

    async def _safe_finish_export(self, **values) -> None:
        try:
            await self.repo.finish_archive_export(**values)
        except Exception:
            log.exception("Failed to finalize message archive export audit")

    @staticmethod
    def _chat_button_text(chat: dict) -> str:
        title = str(chat.get("current_title") or "Без названия")
        chat_type = str(chat.get("chat_type") or "chat")
        return f"{title[:45]} · {chat_type} · #{chat['id']}"

    def _register(self) -> None:
        self.router.message.register(self._command, Command("сообщение"))
        self.router.callback_query.register(
            self._choose_chat,
            F.data.startswith(self.CALLBACK_PREFIX),
        )
