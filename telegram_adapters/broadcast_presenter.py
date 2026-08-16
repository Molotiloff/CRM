from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message

from telegram_adapters.broadcast_models import (
    BroadcastPayload,
    MediaGroupBroadcastPayload,
    PhotoBroadcastPayload,
    TextBroadcastPayload,
)


class AiogramBroadcastPresenter:
    BROADCAST_PROMPT_TEXT = (
        "Прикрепите сообщение и картинку для рассылки "
        "в ответ на это сообщение."
    )
    CB_CONFIRM = "broadcast:confirm"
    CB_CANCEL = "broadcast:cancel"

    @classmethod
    def build_confirm_keyboard(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Отправить рассылку",
                        callback_data=cls.CB_CONFIRM,
                    ),
                    InlineKeyboardButton(
                        text="❌ Отменить рассылку",
                        callback_data=cls.CB_CANCEL,
                    ),
                ]
            ]
        )

    async def preview_single(
        self,
        *,
        source_message: Message,
        target_group: str | None,
    ) -> tuple[int, BroadcastPayload] | None:
        if source_message.photo:
            payload = PhotoBroadcastPayload(
                file_id=source_message.photo[-1].file_id,
                caption=source_message.caption,
                caption_entities=tuple(source_message.caption_entities or ()),
                group=target_group,
            )
            preview = await source_message.answer_photo(
                photo=payload.file_id,
                caption=payload.caption,
                caption_entities=list(payload.caption_entities),
                reply_markup=self.build_confirm_keyboard(),
            )
            return preview.message_id, payload

        if source_message.text:
            payload = TextBroadcastPayload(
                text=source_message.text,
                entities=tuple(source_message.entities or ()),
                group=target_group,
            )
            preview = await source_message.answer(
                text=payload.text,
                entities=list(payload.entities),
                reply_markup=self.build_confirm_keyboard(),
            )
            return preview.message_id, payload

        await source_message.answer("Неподдерживаемый формат сообщения для рассылки.")
        return None

    async def preview_media_group(
        self,
        *,
        messages: list[Message],
        target_group: str | None,
        target_label: str,
    ) -> tuple[int, BroadcastPayload] | None:
        messages = sorted(messages, key=lambda message: message.message_id)
        photos = [message for message in messages if message.photo]
        if not photos:
            await messages[0].answer("Для альбома не найдено фотографий.")
            return None

        caption_message = next((message for message in photos if message.caption), None)
        caption = caption_message.caption if caption_message else None
        caption_entities = tuple(caption_message.caption_entities or ()) if caption_message else ()
        file_ids = tuple(message.photo[-1].file_id for message in photos)
        media = [
            InputMediaPhoto(
                media=file_id,
                caption=caption if index == 0 else None,
                caption_entities=list(caption_entities) if index == 0 else None,
            )
            for index, file_id in enumerate(file_ids)
        ]

        await messages[0].bot.send_media_group(chat_id=messages[0].chat.id, media=media)
        control = await messages[0].answer(
            f"Подтвердите рассылку альбома {target_label}:",
            reply_markup=self.build_confirm_keyboard(),
        )
        return control.message_id, MediaGroupBroadcastPayload(
            file_ids=file_ids,
            caption=caption,
            caption_entities=caption_entities,
            group=target_group,
        )
