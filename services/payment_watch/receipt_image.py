from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    Image = None
    ImageDraw = None
    ImageFont = None


log = logging.getLogger("payment_watch")


def _fmt(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.001"))
    return f"{q:,.3f}".replace(",", "’")


def _fmt_local_tx_datetime(block_ts: datetime | None) -> str:
    if block_ts is None:
        return "—"
    local_dt = block_ts + timedelta(hours=5)
    months = {
        1: "янв",
        2: "фев",
        3: "мар",
        4: "апр",
        5: "мая",
        6: "июн",
        7: "июл",
        8: "авг",
        9: "сен",
        10: "окт",
        11: "ноя",
        12: "дек",
    }
    return f"{local_dt.day} {months[local_dt.month]} {local_dt.year} {local_dt:%H:%M}"


def _pick_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    if ImageFont is None:
        raise RuntimeError("Pillow is not installed")
    candidates: list[str] = []
    if bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            ]
        )
    else:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            ]
        )

    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


@dataclass(frozen=True, slots=True)
class PaymentReceiptImageBuilder:
    width: int = 980
    height: int = 760
    background_color: str = "#18181B"
    plate_color: str = "#2A2930"
    accent_color: str = "#14D97B"
    text_primary: str = "#F4F4F5"
    text_secondary: str = "#B7B7BC"
    divider_color: str = "#3B3A43"

    def build_main_success(
        self,
        *,
        amount: Decimal,
        recipient_address: str,
        tx_hash: str,
        block_ts: datetime | None = None,
    ) -> bytes:
        if Image is None or ImageDraw is None or ImageFont is None:
            raise RuntimeError("Pillow is not installed")
        image = Image.new("RGB", (self.width, self.height), self.background_color)
        draw = ImageDraw.Draw(image)

        amount_font = _pick_font(72, bold=True)
        label_font = _pick_font(38, bold=True)
        value_font = _pick_font(38, bold=True)

        amount_text = f"{_fmt(amount)} USDT"
        top_y = 70
        self._draw_centered_text(draw, y=top_y, text=amount_text, font=amount_font, fill=self.text_primary)

        plate_x1 = 72
        plate_x2 = self.width - 72
        plate_y1 = 230
        plate_y2 = 700
        draw.rounded_rectangle(
            (plate_x1, plate_y1, plate_x2, plate_y2),
            radius=32,
            fill=self.plate_color,
        )

        pad_x = 38
        pad_top = 34
        pad_bottom = 28
        row_left_x = plate_x1 + pad_x
        row_right_x = plate_x2 - pad_x
        top_inner = plate_y1 + pad_top
        bottom_inner = plate_y2 - pad_bottom
        row_band = (bottom_inner - top_inner) / 4

        divider1_y = round(top_inner + row_band)
        divider2_y = round(top_inner + row_band * 2)
        divider3_y = round(top_inner + row_band * 3)

        tx_text, tx_font = self._fit_text_font(
            text=tx_hash,
            initial_size=38,
            min_size=24,
            max_width=390,
        )
        recipient_text, recipient_font = self._fit_text_font(
            text=recipient_address,
            initial_size=38,
            min_size=24,
            max_width=390,
        )
        datetime_text, datetime_font = self._fit_text_font(
            text=_fmt_local_tx_datetime(block_ts),
            initial_size=28,
            min_size=24,
            max_width=390,
        )

        status_baseline_y = self._compute_row_baseline_y(
            draw,
            band_top=top_inner,
            band_bottom=divider1_y,
            left_text="Статус",
            left_font=label_font,
            right_text="Завершено",
            right_font=value_font,
        )
        tx_baseline_y = self._compute_row_baseline_y(
            draw,
            band_top=divider1_y,
            band_bottom=divider2_y,
            left_text="Хэш транзакции",
            left_font=label_font,
            right_text=tx_text,
            right_font=tx_font,
        )
        recipient_baseline_y = self._compute_row_baseline_y(
            draw,
            band_top=divider2_y,
            band_bottom=divider3_y,
            left_text="Получатель",
            left_font=label_font,
            right_text=recipient_text,
            right_font=recipient_font,
        )
        datetime_baseline_y = self._compute_row_baseline_y(
            draw,
            band_top=divider3_y,
            band_bottom=bottom_inner,
            left_text="Дата и время",
            left_font=label_font,
            right_text=datetime_text,
            right_font=datetime_font,
        )

        self._draw_left_baseline_text(
            draw,
            x=row_left_x,
            y=status_baseline_y,
            text="Статус",
            font=label_font,
            fill=self.text_secondary,
        )
        self._draw_right_baseline_text(
            draw,
            x=row_right_x,
            y=status_baseline_y,
            text="Завершено",
            font=value_font,
            fill=self.accent_color,
        )

        draw.line(
            (plate_x1 + pad_x, divider1_y, plate_x2 - pad_x, divider1_y),
            fill=self.divider_color,
            width=2,
        )

        self._draw_left_baseline_text(
            draw,
            x=row_left_x,
            y=tx_baseline_y,
            text="Хэш транзакции",
            font=label_font,
            fill=self.text_secondary,
        )
        self._draw_fitted_right_aligned_text(
            draw,
            x=row_right_x,
            y=tx_baseline_y,
            text=tx_text,
            font=tx_font,
            fill=self.text_primary,
        )

        draw.line(
            (plate_x1 + pad_x, divider2_y, plate_x2 - pad_x, divider2_y),
            fill=self.divider_color,
            width=2,
        )

        self._draw_left_baseline_text(
            draw,
            x=row_left_x,
            y=recipient_baseline_y,
            text="Получатель",
            font=label_font,
            fill=self.text_secondary,
        )
        self._draw_fitted_right_aligned_text(
            draw,
            x=row_right_x,
            y=recipient_baseline_y,
            text=recipient_text,
            font=recipient_font,
            fill=self.text_primary,
        )

        draw.line(
            (plate_x1 + pad_x, divider3_y, plate_x2 - pad_x, divider3_y),
            fill=self.divider_color,
            width=2,
        )

        self._draw_left_baseline_text(
            draw,
            x=row_left_x,
            y=datetime_baseline_y,
            text="Дата и время",
            font=label_font,
            fill=self.text_secondary,
        )
        self._draw_fitted_right_aligned_text(
            draw,
            x=row_right_x,
            y=datetime_baseline_y,
            text=datetime_text,
            font=datetime_font,
            fill=self.text_primary,
        )

        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def _draw_centered_text(self, draw: ImageDraw.ImageDraw, *, y: int, text: str, font, fill: str) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (self.width - text_w) // 2
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_left_baseline_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        font,
        fill: str,
    ) -> None:
        draw.text((x, y), text, font=font, fill=fill, anchor="ls")

    def _draw_right_baseline_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        font,
        fill: str,
    ) -> None:
        draw.text((x, y), text, font=font, fill=fill, anchor="rs")

    def _draw_fitted_right_aligned_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        font,
        fill: str,
    ) -> None:
        draw.text((x, y), text, font=font, fill=fill, anchor="rs")

    def _fit_text_font(
        self,
        *,
        text: str,
        initial_size: int,
        min_size: int,
        max_width: int,
    ) -> tuple[str, ImageFont.ImageFont]:
        if Image is None or ImageDraw is None or ImageFont is None:
            raise RuntimeError("Pillow is not installed")
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        for size in range(initial_size, min_size - 1, -2):
            font = _pick_font(size, bold=True)
            bbox = probe.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            if text_w <= max_width:
                return text, font

        fallback = f"{text[:10]}...{text[-8:]}" if len(text) > 21 else text
        return fallback, _pick_font(min_size, bold=True)

    def _compute_row_baseline_y(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        band_top: int,
        band_bottom: int,
        left_text: str,
        left_font,
        right_text: str,
        right_font,
    ) -> int:
        left_bbox = draw.textbbox((0, 0), left_text, font=left_font, anchor="ls")
        right_bbox = draw.textbbox((0, 0), right_text, font=right_font, anchor="rs")
        top = min(left_bbox[1], right_bbox[1])
        bottom = max(left_bbox[3], right_bbox[3])
        center_y = (band_top + band_bottom) / 2
        baseline_y = center_y - ((top + bottom) / 2)
        return round(baseline_y)
