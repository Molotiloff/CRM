from __future__ import annotations

from decimal import Decimal
from html import escape

from services.xe_api import XEConvertResult


def format_decimal_compact(value: Decimal, places: int) -> str:
    q = Decimal(10) ** -places
    value = value.quantize(q)
    s = f"{value.normalize():f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    return s


def format_decimal_2(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"


def format_decimal_3(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.001')):f}"


def format_percent(value: Decimal) -> str:
    return format_decimal_compact(value, 3).replace(".", ",")


def format_amount(value: Decimal) -> str:
    compact = format_decimal_compact(value, 8)
    if "." in compact:
        head, tail = compact.split(".", 1)
        if len(tail) > 2:
            tail = tail[:2].rstrip("0")
            return head if not tail else f"{head}.{tail}"
    return compact


def format_url_amount(value: Decimal) -> str:
    return format_decimal_compact(value, 8)


class ResponseFormatter:
    XE_URL_TEMPLATE = (
        "https://www.xe.com/currencyconverter/convert/"
        "?Amount={amount}&From={from_currency}&To={to_currency}"
    )

    SKYEX_URL = "https://t.me/skyex_world"

    def build_message_text(self, result: XEConvertResult) -> str:
        safe_skyex = escape(self.SKYEX_URL, quote=True)
        safe_xe_url = escape(self._build_xe_url(result), quote=True)

        amount_str = escape(format_amount(result.amount))
        from_currency = escape(result.from_currency)
        to_currency = escape(result.to_currency)
        rate_str = escape(format_decimal_compact(result.rate, 4))

        final_str = (
            escape(format_decimal_3(result.final_amount))
            if result.final_amount is not None
            else escape(format_decimal_2(result.converted))
        )

        header = self._build_header(result, amount_str, final_str, from_currency, to_currency)
        calc_block = self._build_calc_block(result)

        return (
            f"{header}\n"
            f"-----\n"
            f"Кросс по "
            f'<a href="{safe_xe_url}">xe.com</a> '
            f"1 {from_currency} = {rate_str} {to_currency}\n"
            f"-----\n"
            f"{calc_block}\n"
            f"----\n"
            f'<a href="{safe_skyex}">Powered by SKYEX</a>'
        )

    def _build_header(
        self,
        result: XEConvertResult,
        amount_str: str,
        final_str: str,
        from_currency: str,
        to_currency: str,
    ) -> str:
        if result.percent is None:
            return f"{amount_str} {from_currency} = {final_str} {to_currency}"

        sign_symbol = "+" if result.sign > 0 else "-"
        percent_str = escape(format_percent(result.percent))
        suffix = "%%" if result.is_markup else "%"
        return f"{amount_str} {from_currency} {sign_symbol} {percent_str}{suffix} = {final_str} {to_currency}"

    def _build_calc_block(self, result: XEConvertResult) -> str:
        amount_str = escape(format_amount(result.amount))
        rate_formula_str = escape(format_decimal_compact(result.rate, 4))
        converted_str = escape(format_decimal_2(result.converted))

        first_line = f"{amount_str}*{rate_formula_str} = {converted_str}"

        if result.percent is None:
            return first_line

        final_str = escape(format_decimal_3(result.final_amount))
        percent_str = escape(format_percent(result.percent))
        percent_fraction = result.percent / Decimal("100")

        if result.is_markup:
            if result.sign > 0:
                second_line = f"{converted_str}/(1-{percent_str}%) = {final_str}"
            else:
                divisor = escape(format_decimal_compact(Decimal("1") + percent_fraction, 4))
                second_line = f"{converted_str}/{divisor} = {final_str}"
            return f"{first_line}\n{second_line}"

        if result.sign > 0:
            second_line = f"{converted_str}+{percent_str}% = {final_str}"
        else:
            second_line = f"{converted_str}-{percent_str}% = {final_str}"

        return f"{first_line}\n{second_line}"

    def _build_xe_url(self, result: XEConvertResult) -> str:
        return self.XE_URL_TEMPLATE.format(
            amount=format_url_amount(result.amount),
            from_currency=result.from_currency,
            to_currency=result.to_currency,
        )
