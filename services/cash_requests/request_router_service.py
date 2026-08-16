from __future__ import annotations

from collections.abc import Mapping


class RequestRouterService:
    def __init__(
        self,
        *,
        request_chat_id: int | None = None,
        city_cash_chats: Mapping[str, int] | None = None,
        city_schedule_chats: Mapping[str, int] | None = None,
        default_city: str = "екб",
    ) -> None:
        self.request_chat_id = int(request_chat_id) if request_chat_id is not None else None

        self.city_cash_chats: dict[str, int] = {
            str(city).strip().lower(): int(chat_id)
            for city, chat_id in (city_cash_chats or {}).items()
        }

        self.city_schedule_chats: dict[str, int] = {
            str(city).strip().lower(): int(chat_id)
            for city, chat_id in (city_schedule_chats or {}).items()
        }

        self.default_city = (default_city or "екб").strip().lower()

        self._request_chat_to_city: dict[int, str] = {
            int(chat_id): city for city, chat_id in self.city_cash_chats.items()
        }

        self._schedule_chat_to_city: dict[int, str] = {
            int(chat_id): city for city, chat_id in self.city_schedule_chats.items()
        }

    @property
    def city_keys(self) -> set[str]:
        return set(self.city_cash_chats.keys())

    def normalize_city(self, city: str | None) -> str:
        city_norm = (city or "").strip().lower()
        return city_norm or self.default_city

    def pick_request_chat_for_city(self, city: str | None) -> int | None:
        city_norm = (city or "").strip().lower()

        if city_norm:
            if city_norm in self.city_cash_chats:
                return self.city_cash_chats[city_norm]
            return self.request_chat_id

        if self.default_city in self.city_cash_chats:
            return self.city_cash_chats[self.default_city]

        return self.request_chat_id

    def pick_schedule_chat_for_city(self, city: str | None) -> int | None:
        city_norm = (city or "").strip().lower()

        if city_norm:
            return self.city_schedule_chats.get(city_norm)

        return self.city_schedule_chats.get(self.default_city)

    def is_request_chat(self, chat_id: int) -> bool:
        chat_id = int(chat_id)

        if chat_id in self._request_chat_to_city:
            return True

        return self.request_chat_id is not None and chat_id == self.request_chat_id

    def is_schedule_chat(self, chat_id: int) -> bool:
        return int(chat_id) in self._schedule_chat_to_city

    def city_by_request_chat(self, chat_id: int) -> str | None:
        chat_id = int(chat_id)

        city = self._request_chat_to_city.get(chat_id)
        if city:
            return city

        if self.request_chat_id is not None and chat_id == self.request_chat_id:
            return self.default_city

        return None

    def city_by_schedule_chat(self, chat_id: int) -> str | None:
        return self._schedule_chat_to_city.get(int(chat_id))

    def help_text(self) -> str:
        request_cities = (
            ", ".join(sorted(self.city_cash_chats.keys())) if self.city_cash_chats else "—"
        )
        schedule_cities = (
            ", ".join(sorted(self.city_schedule_chats.keys())) if self.city_schedule_chats else "—"
        )

        return (
            "Форматы:\n"
            "• /депр [город] <сумма/expr> [Принимает] [Выдает] [! комментарий]\n"
            "• /выдр [город] <сумма/expr> [Выдает] [Принимает] [! комментарий]\n"
            "• /првд [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /пдвр [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /прве [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n\n"
            f"Города заявок: {request_cities}\n"
            f"Города расписания: {schedule_cities}\n"
            f"Если город не указан — по умолчанию: {self.default_city}\n\n"
            "Важно для /прв*: суммы должны быть 2 отдельными токенами.\n"
            "Напр.: /првд члб (700+300) 1000 @cashier @client ! коммент\n\n"
            "Редактирование:\n"
            "• ответьте командой на карточку БОТА — можно менять сумму, город, контакты, комментарий;\n"
            "• тип и валюты менять нельзя.\n\n"
            "В чатах заявок:\n"
            "• ответьте на карточку командой /время 10:00 — добавит/заменит строку времени;\n"
            "• после установки времени заявка попадёт в чат расписания соответствующего города."
        )
