from __future__ import annotations


class RequestTableMessageBuilder:
    STATUS_LINE_DONE = "Статус: Занесена в таблицу ✅"
    STATUS_LINE_DELETED = "Статус: Удалено из таблиц 🗑️"

    @staticmethod
    def append_status_once(text: str, status_line: str) -> str:
        src = text or ""
        if status_line in src:
            return src
        return src.rstrip() + "\n" + status_line

    @staticmethod
    def short(text: str, limit: int = 180) -> str:
        return text if len(text) <= limit else (text[: limit - 1] + "…")

    def done_summary(self, *, result) -> str:
        return self.short(
            f"Занесена в таблицу ✅ ({result.sheet_type}, {result.in_cur}→{result.out_cur}, "
            f"получено {result.in_amt}, отдано {result.out_amt}, курс {result.rate})"
        )

    def deleted_status(self, req_id: str) -> str:
        return f"{self.STATUS_LINE_DELETED} (#{req_id})"

    @staticmethod
    def deleted_summary(*, deleted_buy: int, deleted_sale: int) -> str:
        return f"Удалено из таблиц: Покупка={deleted_buy}, Продажа={deleted_sale}"
