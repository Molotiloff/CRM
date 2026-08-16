from __future__ import annotations

import logging

from db_asyncpg.ports.cash import RequestScheduleRepositoryPort
from services.cash_requests.models import ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService
from services.messaging import MessengerError, MessengerPort

log = logging.getLogger("request_schedule")


class RequestScheduleService:
    def __init__(
        self,
        *,
        repo: RequestScheduleRepositoryPort,
        router_service: RequestRouterService,
    ) -> None:
        self.repo = repo
        self.router_service = router_service

    @staticmethod
    def _norm_city(city: str) -> str:
        return (city or "").strip().lower()

    @staticmethod
    def _norm_hhmm(hhmm: str | None) -> str | None:
        value = (hhmm or "").strip()
        return value or None

    @staticmethod
    def _decorate_line(line_text: str) -> str:
        text = (line_text or "").strip()
        if text.startswith("-"):
            return f"🟥 {text}"
        if text.startswith("+"):
            return f"🟩 {text}"
        return text

    async def upsert_entry(self, entry: ScheduleEntry) -> None:
        await self.repo.upsert_request_schedule_entry(
            req_id=entry.req_id,
            city=self._norm_city(entry.city),
            hhmm=self._norm_hhmm(entry.hhmm),
            request_kind=entry.request_kind,
            line_text=entry.line_text,
            client_name=entry.client_name,
            request_chat_id=entry.request_chat_id,
            request_message_id=entry.request_message_id,
        )

    async def remove_entry(self, *, req_id: str) -> bool:
        return await self.repo.deactivate_request_schedule_entry(req_id=req_id)

    async def render_board(self, city: str) -> str:
        city_norm = self._norm_city(city)
        rows = await self.repo.list_request_schedule_entries(city=city_norm)

        with_time: list[dict] = []
        without_time: list[dict] = []

        for item in rows:
            hhmm = self._norm_hhmm(item.get("hhmm"))
            if hhmm is not None:
                row = dict(item)
                row["hhmm"] = hhmm
                with_time.append(row)
            else:
                without_time.append(dict(item))

        with_time.sort(key=lambda x: (x["hhmm"], x.get("updated_at"), x.get("req_id")))
        without_time.sort(key=lambda x: (x.get("updated_at"), x.get("req_id")))

        lines = ["📋 <b>Ближайшие клиенты</b>", ""]

        if with_time:
            for item in with_time:
                decorated = self._decorate_line(item["line_text"])
                lines.append(f"<code>{item['hhmm']}</code> — {decorated}")
        else:
            lines.append("Пока пусто")

        if without_time:
            lines += ["", "Клиенты без назначенного времени", ""]
            for item in without_time:
                decorated = self._decorate_line(item["line_text"])
                lines.append(decorated)

        return "\n".join(lines)

    async def sync_board(self, *, messenger: MessengerPort, city: str) -> None:
        city_norm = self._norm_city(city)
        schedule_chat_id = self.router_service.pick_schedule_chat_for_city(city_norm)
        if not schedule_chat_id:
            log.info("Schedule board skipped: no schedule chat configured for city=%s", city_norm)
            return

        text = await self.render_board(city_norm)
        board = await self.repo.get_request_schedule_board(city=city_norm)

        if board:
            board_chat_id = int(board["board_chat_id"])
            board_message_id = int(board["board_message_id"])

            log.info(
                "Trying to edit schedule board city=%s chat_id=%s message_id=%s",
                city_norm,
                board_chat_id,
                board_message_id,
            )

            try:
                await messenger.edit_text(
                    chat_id=board_chat_id,
                    message_id=board_message_id,
                    text=text,
                )
                log.info(
                    "Schedule board updated city=%s chat_id=%s message_id=%s",
                    city_norm,
                    board_chat_id,
                    board_message_id,
                )
                return

            except MessengerError as e:
                err = str(e).lower()

                if "message is not modified" in err:
                    log.info(
                        "Schedule board not modified city=%s chat_id=%s message_id=%s",
                        city_norm,
                        board_chat_id,
                        board_message_id,
                    )
                    return

                log.warning(
                    "Failed to edit schedule board city=%s chat_id=%s message_id=%s: %s",
                    city_norm,
                    board_chat_id,
                    board_message_id,
                    e,
                )

                if "message to edit not found" in err or "message can't be edited" in err:
                    try:
                        await messenger.delete(
                            chat_id=board_chat_id,
                            message_id=board_message_id,
                        )
                        log.info(
                            "Old schedule board deleted city=%s chat_id=%s message_id=%s",
                            city_norm,
                            board_chat_id,
                            board_message_id,
                        )
                    except MessengerError as del_e:
                        log.warning(
                            "Failed to delete old schedule board city=%s chat_id=%s message_id=%s: %r",
                            city_norm,
                            board_chat_id,
                            board_message_id,
                            del_e,
                        )
                else:
                    log.warning(
                        "Schedule board edit error is not recoverable by resend city=%s chat_id=%s message_id=%s",
                        city_norm,
                        board_chat_id,
                        board_message_id,
                    )
                    return

            except Exception:
                log.exception(
                    "Unexpected error editing schedule board city=%s chat_id=%s message_id=%s",
                    city_norm,
                    board_chat_id,
                    board_message_id,
                )
                return

        msg = await messenger.send(
            chat_id=int(schedule_chat_id),
            text=text,
        )

        log.info(
            "New schedule board created city=%s chat_id=%s message_id=%s",
            city_norm,
            int(msg.chat_id),
            int(msg.message_id),
        )

        await self.repo.upsert_request_schedule_board(
            city=city_norm,
            board_chat_id=int(msg.chat_id),
            board_message_id=int(msg.message_id),
        )
