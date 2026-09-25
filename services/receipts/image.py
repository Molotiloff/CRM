from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    Image = None
    ImageDraw = None
    ImageFont = None

from services.number_formatting import format_amount_core

_LOCAL_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
_LOGO_PATH = Path(__file__).parent / "assets" / "skyex_logo.png"
_MONTHS = (
    "", "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)


def format_receipt_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local = value.astimezone(_LOCAL_TIMEZONE)
    return f"{local.day} {_MONTHS[local.month]} {local.year} {local:%H:%M}"


def _pick_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    if ImageFont is None:
        raise RuntimeError("Pillow is not installed")
    candidates = (
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        )
        if bold else (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        )
    )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


@dataclass(frozen=True, slots=True)
class ReceiptRow:
    label: str
    value: str
    accent: bool = False


@dataclass(frozen=True, slots=True)
class ReceiptImageBuilder:
    width: int = 980
    background_color: str = "#18181B"
    plate_color: str = "#2A2930"
    accent_color: str = "#14D97B"
    text_primary: str = "#F4F4F5"
    text_secondary: str = "#B7B7BC"
    divider_color: str = "#3B3A43"

    def build(
        self,
        *,
        amount: Decimal,
        currency: str,
        precision: int,
        rows: tuple[ReceiptRow, ...],
    ) -> bytes:
        if Image is None or ImageDraw is None or ImageFont is None:
            raise RuntimeError("Pillow is not installed")
        if not rows:
            raise ValueError("Receipt must contain at least one row")

        label_font = _pick_font(34, bold=True)
        value_font = _pick_font(34, bold=True)
        wrapped_font = _pick_font(29, bold=True)
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        layouts: list[tuple[ReceiptRow, tuple[str, ...], bool, int]] = []
        for row in rows:
            fits_side = self._text_width(probe, row.value, value_font) <= 420
            lines = (row.value,) if fits_side else self._wrap(probe, row.value, wrapped_font, 760)
            row_height = 110 if fits_side else max(138, 70 + 42 * len(lines))
            layouts.append((row, lines, fits_side, row_height))

        plate_top = 230
        plate_bottom = plate_top + 34 + sum(item[3] for item in layouts) + 28
        image = Image.new("RGB", (self.width, plate_bottom + 60), self.background_color)
        self._add_logo_watermark(image)
        draw = ImageDraw.Draw(image)
        title = f"{format_amount_core(amount, precision)} {currency.upper()}"
        title_font = self._fitted_font(draw, title, start=72, minimum=32, max_width=860)
        title_width = self._text_width(draw, title, title_font)
        draw.text(((self.width - title_width) // 2, 70), title, font=title_font, fill=self.text_primary)

        draw.rounded_rectangle(
            (72, plate_top, self.width - 72, plate_bottom), radius=32, fill=self.plate_color
        )
        top = plate_top + 34
        for index, (row, lines, fits_side, row_height) in enumerate(layouts):
            color = self.accent_color if row.accent else self.text_primary
            if fits_side:
                baseline = top + row_height // 2 + 12
                draw.text((110, baseline), row.label, font=label_font, fill=self.text_secondary, anchor="ls")
                draw.text((870, baseline), row.value, font=value_font, fill=color, anchor="rs")
            else:
                draw.text((110, top + 20), row.label, font=label_font, fill=self.text_secondary)
                for line_index, line in enumerate(lines):
                    draw.text((110, top + 68 + 42 * line_index), line, font=wrapped_font, fill=color)
            top += row_height
            if index + 1 < len(layouts):
                draw.line((110, top, 870, top), fill=self.divider_color, width=2)

        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def _add_logo_watermark(self, image: Image.Image) -> None:
        if Image is None:
            raise RuntimeError("Pillow is not installed")
        with Image.open(_LOGO_PATH) as source:
            scale = self.width * 1.08 / source.width
            size = (round(source.width * scale), round(source.height * scale))
            luminance = source.convert("L").resize(size, Image.Resampling.LANCZOS)
        # Only the light logo strokes become visible; the purple source background stays hidden.
        opacity = luminance.point(lambda value: max(0, min(36, round((value - 135) * 0.35))))
        watermark = Image.new("RGB", size, "#B7B7BC")
        image.paste(
            watermark,
            ((image.width - size[0]) // 2, (image.height - size[1]) // 2 + 30),
            opacity,
        )

    @staticmethod
    def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]

    def _fitted_font(
        self, draw: ImageDraw.ImageDraw, text: str, *, start: int, minimum: int, max_width: int
    ) -> ImageFont.ImageFont:
        for size in range(start, minimum - 1, -2):
            font = _pick_font(size, bold=True)
            if self._text_width(draw, text, font) <= max_width:
                return font
        return _pick_font(minimum, bold=True)

    def _wrap(
        self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int
    ) -> tuple[str, ...]:
        lines: list[str] = []
        remaining = text
        while remaining:
            if self._text_width(draw, remaining, font) <= max_width:
                lines.append(remaining)
                break
            split_at = len(remaining)
            while split_at > 1 and self._text_width(draw, remaining[:split_at], font) > max_width:
                split_at -= 1
            space_at = remaining.rfind(" ", 0, split_at + 1)
            if space_at > 0:
                split_at = space_at
            lines.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        return tuple(lines)


class PaymentReceiptImageBuilder(ReceiptImageBuilder):
    def build_main_success(
        self,
        *,
        amount: Decimal,
        recipient_address: str,
        tx_hash: str,
        block_ts: datetime | None = None,
    ) -> bytes:
        shortened_hash = f"{tx_hash[:10]}...{tx_hash[-8:]}" if len(tx_hash) > 21 else tx_hash
        shortened_address = (
            f"{recipient_address[:10]}...{recipient_address[-8:]}"
            if len(recipient_address) > 28 else recipient_address
        )
        return self.build(
            amount=amount,
            currency="USDT",
            precision=3,
            rows=(
                ReceiptRow("Статус", "Завершено", accent=True),
                ReceiptRow("Хэш транзакции", shortened_hash),
                ReceiptRow("Получатель", shortened_address),
                ReceiptRow("Дата и время", format_receipt_datetime(block_ts)),
            ),
        )
