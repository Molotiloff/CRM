"""SKYEX AML report: vector A4 PDF, built entirely in memory.

Only two immutable fonts are registered globally. No report, canvas, image or
PDF buffer is cached. Invoke via SerializedThreadExecutor, outside the event loop.
"""
from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from services.aml.models import AMLCheckRequest

_ASSETS = Path(__file__).with_name("assets")
_FONT_LOCK = threading.Lock()
_REGULAR = "SkyexAML"
_BOLD = "SkyexAML-Bold"
_BG = "#11151B"
_CARD = "#1C232D"
_WHITE = "#F3F5FA"
_MUTED = "#ABB7C7"
_LINE = "#354252"
_BLUE = "#1EA5D3"
_GREEN = "#5BD3A0"
_AMBER = "#FFBC4B"
_RED = "#FF6B82"
_WIDTH, _HEIGHT = A4
_MARGIN = 35
_CONTENT = _WIDTH - 2 * _MARGIN


def _register_fonts() -> None:
    with _FONT_LOCK:
        if _REGULAR not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(_REGULAR, str(_ASSETS / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont(_BOLD, str(_ASSETS / "DejaVuSans-Bold.ttf")))


def _wrap(value: str, width: float, size: float, *, bold: bool = False) -> list[str]:
    """Wrap long addresses as well as words, without dropping any characters."""
    font = _BOLD if bold else _REGULAR
    lines: list[str] = []
    for paragraph in value.splitlines() or [""]:
        line = ""
        for char in paragraph:
            if line and pdfmetrics.stringWidth(line + char, font, size) > width:
                split = line.rfind(" ")
                if split > 0:
                    lines.append(line[:split])
                    line = line[split + 1:]
                else:
                    lines.append(line)
                    line = ""
            line += char
        lines.append(line)
    return lines


class AMLReportPDFBuilder:
    def build(
        self, report: Mapping[str, Any], *, request: AMLCheckRequest,
        wallet_balance: str | None = None,
    ) -> bytes:
        _register_fonts()
        with BytesIO() as output:
            canvas = Canvas(output, pagesize=A4, pageCompression=1)
            canvas.setTitle("SKYEX - AML report")
            canvas.setAuthor("SKYEX")
            document = _ReportPage(canvas, report, request, wallet_balance)
            try:
                document.draw()
                canvas.save()
                return output.getvalue()
            finally:
                # ReportLab has per-document font state in weak-key dictionaries;
                # neither the canvas nor its document may escape this invocation.
                del document
                del canvas


class _ReportPage:
    def __init__(self, canvas: Canvas, report: Mapping[str, Any], request: AMLCheckRequest, balance: str | None):
        self.c = canvas
        self.report = report
        self.request = request
        self.balance = balance
        self.asset = str(report.get("token") or report.get("asset_name") or "-")
        self.url = str(report.get("preview_link") or "")
        parsed = urlparse(self.url)
        if parsed.scheme != "https" or parsed.hostname not in {"getblock.net", "www.getblock.net"}:
            self.url = ""
        self.report_id = urlparse(self.url).path.rstrip("/").rsplit("/", 1)[-1] if self.url else "-"

    def text(self, x: float, y: float, text: str, size: float = 9, color: str = _WHITE, *, bold: bool = False) -> None:
        self.c.setFillColor(HexColor(color))
        self.c.setFont(_BOLD if bold else _REGULAR, size)
        self.c.drawString(x, _HEIGHT - y, text)

    def box(self, x: float, y: float, w: float, h: float, color: str = _CARD, radius: float = 12) -> None:
        self.c.setFillColor(HexColor(color))
        self.c.roundRect(x, _HEIGHT - y - h, w, h, radius, stroke=0, fill=1)

    def line(self, x: float, y: float, w: float) -> None:
        self.c.setStrokeColor(HexColor(_LINE))
        self.c.setLineWidth(0.6)
        self.c.line(x, _HEIGHT - y, x + w, _HEIGHT - y)

    def header(self, page: int) -> None:
        self.box(0, 0, _WIDTH, _HEIGHT, _BG, 0)
        self.box(0, 0, _WIDTH, 7, _BLUE, 0)
        self.box(_MARGIN, 24, _CONTENT, 74)
        self.c.drawImage(str(_ASSETS / "skyex_logo.png"), 55, _HEIGHT - 90, width=64, height=60, mask="auto", preserveAspectRatio=True)
        self.text(133, 61, "SKY", 23, bold=True)
        self.text(186, 61, "EX", 23, _BLUE, bold=True)
        self.text(133, 82, "AML / ПРОВЕРКА " + ("ТРАНЗАКЦИИ" if self.request.kind == "transaction" else "АДРЕСА"), 8, _MUTED, bold=True)
        self.text(448, 55, "ОТЧЁТ О РИСКАХ", 8.5, bold=True)
        date = str(self.report.get("report_date") or "Дата не указана")
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", date):
            date += " UTC+5"
        date_lines = _wrap(date, 114, 7.6)
        for index, date_line in enumerate(date_lines):
            self.text(447, 74 + index * 10, date_line, 7.6, _MUTED)
        self.text(38, 125, "Результат AML-проверки", 19, bold=True)
        network = {"trc20": "TRON", "erc20": "Ethereum", "bep20": "BNB Smart Chain", "btc": "Bitcoin"}[self.request.network]
        self.text(38, 144, f"Сеть: {network} · {self.asset}" + (f" · Продолжение, стр. {page}" if page > 1 else ""), 9, _MUTED)

    def risk(self) -> None:
        raw = str(self.report.get("risk_percent") or "")
        match = re.search(r"\d+(?:[.,]\d+)?", raw)
        value = min(100.0, max(0.0, float(match[0].replace(",", ".")))) if match else None
        color, label = (_MUTED, "НЕТ ОЦЕНКИ") if value is None else (
            (_RED, "ВЫСОКИЙ РИСК") if value >= 70 else (_AMBER, "СРЕДНИЙ РИСК") if value >= 31 else (_GREEN, "НИЗКИЙ РИСК")
        )
        self.box(35, 163, _CONTENT, 92)
        self.text(52, 188, "ОБЩИЙ УРОВЕНЬ РИСКА", 8, _MUTED, bold=True)
        self.text(52, 227, raw or "Нет данных", 27, bold=True)
        self.box(282, 193, 111, 29, "#332C25" if color == _AMBER else _BG, 9)
        self.text(292, 211, label, 8.2, color, bold=True)
        for index, segment in enumerate((_GREEN, _AMBER, _RED)):
            self.box(408 + index * 44, 206, 44, 9, segment, 3)
        if value is not None:
            self.c.setFillColor(HexColor(_WHITE))
            self.c.circle(408 + 132 * value / 100, _HEIGHT - 210.5, 4.8, stroke=0, fill=1)
        provider = str(self.report.get("aml_provider") or "Не указан")
        self.text(407, 234, f"Оценка: {provider}", 7.5, _MUTED)
        self.text(407, 247, "Источник: GetBlock", 7.5, _MUTED)

    def details(self) -> float:
        self.text(38, 276, "СВЕДЕНИЯ О ПРОВЕРКЕ", 9, _BLUE, bold=True)
        rows = [
            ("Хэш транзакции" if self.request.kind == "transaction" else "Адрес", str(self.report.get("hash") or self.request.value)),
            ("Актив", self.asset),
            ("Тип объекта", "Транзакция" if self.request.kind == "transaction" else "Адрес"),
            ("Контрагент", str(self.report.get("counterparty") or "unknown")),
        ]
        if self.balance is not None:
            rows.append(("Остаток USDT", self.balance))
        layouts = [(label, _wrap(value, 387, 8.4, bold=index == 0)) for index, (label, value) in enumerate(rows)]
        height = sum(max(23, len(lines) * 12 + 10) for _, lines in layouts) + 16
        if 288 + height > 652:
            raise ValueError("AML report details exceed the A4 page capacity")
        self.box(35, 288, _CONTENT, height)
        y = 311.0
        for index, (label, lines) in enumerate(layouts):
            self.text(51, y, label, 8, _MUTED)
            for line_index, line in enumerate(lines):
                self.text(155, y + 12 * line_index, line, 8.4, bold=index == 0)
            y += max(23, len(lines) * 12 + 10)
            if index < len(layouts) - 1:
                self.line(50, y - 13, _CONTENT - 30)
        return 288 + height

    def sources(self, top: float, groups: list[list[str]], offsets: list[int]) -> None:
        self.text(38, top, "СТРУКТУРА ИСТОЧНИКОВ СРЕДСТВ", 9, _BLUE, bold=True)
        width = (_CONTENT - 18) / 3
        for index, (title, color) in enumerate((("ДОВЕРЕННЫЕ", _GREEN), ("ПОДОЗРИТЕЛЬНЫЕ", _AMBER), ("ОПАСНЫЕ", _RED))):
            x = 35 + index * (width + 9)
            self.box(x, top + 13, width, 660 - top - 13, radius=9)
            self.box(x, top + 13, width, 5, color, 2)
            self.text(x + 11, top + 39, title, 8.2, color, bold=True)
            self.line(x + 11, top + 51, width - 22)
            y = top + 71
            if not groups[index]:
                self.text(x + 11, y, "Нет данных", 7.5, _MUTED)
            while offsets[index] < len(groups[index]):
                item = groups[index][offsets[index]]
                match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", item)
                # Transaction rows may contain a monetary amount after the percentage.
                label = item[:match.start()].strip() if match else item
                tail = item[match.end():].strip() if match else ""
                lines = _wrap(label, width - 62, 7.1)
                tail_lines = _wrap(tail, width - 22, 6.5) if tail else []
                row_height = max(20, len(lines) * 10 + len(tail_lines) * 9 + 7)
                if y + (len(lines) - 1) * 10 + len(tail_lines) * 9 + 4 > 652:
                    break
                for line_index, line in enumerate(lines):
                    self.text(x + 11, y + line_index * 10, line, 7.1, _MUTED)
                if match:
                    percent = match[1] + "%"
                    percent_width = pdfmetrics.stringWidth(percent, _BOLD, 7.2)
                    self.text(x + width - 11 - percent_width, y, percent, 7.2, bold=True)
                for line_index, line in enumerate(tail_lines):
                    self.text(x + 11, y + len(lines) * 10 + line_index * 9, line, 6.5, _MUTED)
                y += row_height
                offsets[index] += 1

    def footer(self, page: int) -> None:
        self.box(35, 672, _CONTENT, 48)
        self.text(49, 690, "ИНТЕРПРЕТАЦИЯ ОЦЕНКИ", 8, bold=True)
        self.text(49, 707, "0-30% - низкий", 8, _GREEN)
        self.text(190, 707, "31-69% - средний", 8, _AMBER)
        self.text(368, 707, "70-100% - высокий", 8, _RED)
        self.line(35, 737, _CONTENT)
        self.text(35, 752, f"ID отчёта: {self.report_id} · Стр. {page}", 7.1, _MUTED)
        self.text(35, 770, "Данные проверки предоставлены AML-провайдером через GetBlock.", 7.1, _MUTED)
        self.text(35, 783, "SKYEX не является первичным поставщиком риск-оценки.", 7.1, _MUTED)
        self.text(35, 804, "sky-ex.ru", 9, _BLUE, bold=True)
        self.c.linkURL("https://sky-ex.ru", (35, _HEIGHT - 807, 100, _HEIGHT - 794), relative=0)
        if self.url:
            self.text(151, 804, "Отчёт GetBlock", 8)
            self.c.linkURL(self.url, (151, _HEIGHT - 807, 235, _HEIGHT - 794), relative=0)
            qr = QrCodeWidget(self.url, barLevel="M")
            x0, y0, x1, y1 = qr.getBounds()
            drawing = Drawing(58, 58, transform=[58 / (x1 - x0), 0, 0, 58 / (y1 - y0), 0, 0])
            drawing.add(qr)
            self.box(502, 764, 58, 58, "#FFFFFF", 0)
            renderPDF.draw(drawing, self.c, 502, _HEIGHT - 822)

    def draw(self) -> None:
        groups = [[str(value) for value in (self.report.get(key) or [])] for key in (
            "trusted_sources", "suspicious_sources", "dangerous_sources",
        )]
        offsets = [0, 0, 0]
        page = 1
        while True:
            self.header(page)
            if page == 1:
                self.risk()
                top = self.details() + 21
            else:
                top = 171
            previous = tuple(offsets)
            if top < 570:
                self.sources(top, groups, offsets)
            else:
                self.text(38, top, "Источники средств - на следующей странице", 9, _MUTED)
            self.footer(page)
            self.c.showPage()
            if all(offsets[index] >= len(group) for index, group in enumerate(groups)):
                break
            if page > 1 and tuple(offsets) == previous:
                raise ValueError("AML source item is too long to fit on an A4 page")
            page += 1
