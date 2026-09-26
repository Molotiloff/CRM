from __future__ import annotations

import gc
import html
import json
import re
from datetime import datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup


def _dispose_soup(soup: BeautifulSoup) -> None:
    soup.decompose()
    # Узлы BeautifulSoup образуют циклы ссылок и иначе удерживают весь HTML до
    # очередного запуска cyclic GC. AML вызывается редко, поэтому освобождаем DOM
    # сразу после разбора.
    gc.collect()


def _first_regex_group(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return None


def extract_csrf_from_html(html_text: str) -> str | None:
    token = _first_regex_group(
        html_text,
        [
            r'csrf-token"\s+content="([^"]+)"',
            r'name="_csrf"\s+value="([^"]+)"',
            r'"_csrf"\s*:\s*"([^"]+)"',
            r"'_csrf'\s*:\s*'([^']+)'",
        ],
    )
    if token:
        return token

    soup = BeautifulSoup(html_text, "html.parser")
    try:
        meta = soup.select_one('meta[name="csrf-token"]')
        if meta and meta.get("content"):
            return str(meta["content"])

        hidden = soup.select_one('input[name="_csrf"]')
        if hidden and hidden.get("value"):
            return str(hidden["value"])
    finally:
        _dispose_soup(soup)

    return None


def find_hidden_csrf_field(html_text: str) -> str | None:
    token = _first_regex_group(
        html_text,
        [
            r'name="_csrf"\s+value="([^"]+)"',
            r'name="[^"]*csrf[^"]*"\s+value="([^"]+)"',
        ],
    )
    if token:
        return token

    soup = BeautifulSoup(html_text, "html.parser")
    try:
        for selector in ('input[name="_csrf"]', 'input[name="csrf"]', 'input[name*="csrf"]'):
            hidden = soup.select_one(selector)
            if hidden and hidden.get("value"):
                return str(hidden["value"])
    finally:
        _dispose_soup(soup)

    return None


def extract_checking_address(html_text: str) -> str | None:
    soup = BeautifulSoup(html_text, "html.parser")
    try:
        field = soup.select_one('input[name="CheckingForm[checking_address]"]')
        if field is None:
            return None
        return str(field.get("value") or "").strip() or None
    finally:
        _dispose_soup(soup)


def extract_amlcheckup(obj: Any) -> str | None:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)

    patterns = [
        r"urlParams\[amlcheckup\][^0-9a-fA-F]*([0-9a-fA-F\-]{36})",
        r"urlParams%5Bamlcheckup%5D=([0-9a-fA-F\-]{36})",
        r"amlcheckup=([0-9a-fA-F\-]{36})",
        r'"amlcheckup"\s*:\s*"([0-9a-fA-F\-]{36})"',
        r"'amlcheckup'\s*:\s*'([0-9a-fA-F\-]{36})'",
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def extract_amlcheckup_from_redirect_header(headers: dict[str, Any]) -> str | None:
    redirect_value = headers.get("x-redirect") or headers.get("X-Redirect")
    if not redirect_value:
        return None
    return extract_amlcheckup(redirect_value)


def _extract_label_value(text: str, label: str) -> str | None:
    pattern = rf"{re.escape(label)}\s*:\s*<span\b[^>]*>(.*?)</span>"
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None

    value = re.sub(r"<.*?>", "", m.group(1), flags=re.S)
    value = html.unescape(value).strip()
    return value or None


def _iter_report_info_paragraphs(soup: BeautifulSoup) -> list[Any]:
    items = soup.select(".report-info .details-info-item p, #report-info .details-info-item p")
    if items:
        return items
    return soup.select("p")


def _extract_label_value_soup(soup: BeautifulSoup, label: str) -> str | None:
    label_norm = label.strip().lower()

    for item in _iter_report_info_paragraphs(soup):
        full_text = re.sub(r"\s+", " ", item.get_text(" ", strip=True)).strip()
        if not full_text.lower().startswith(f"{label_norm}:"):
            continue

        span = item.find("span")
        value = span.get_text(" ", strip=True) if span else full_text.split(":", 1)[-1]
        value = html.unescape(value).strip()
        return value or None

    return None


def _extract_report_value(soup: BeautifulSoup, text: str, label: str) -> str | None:
    return _extract_label_value_soup(soup, label) or _extract_label_value(text, label)


def _extract_percent(text: str) -> str:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if not m:
        return ""
    return f"{m.group(1).replace(',', '.')}%"


def _label_from_risk_text(text: str) -> str:
    text_lower = text.lower()
    if "critical" in text_lower:
        return "Critical risk level"
    if "high" in text_lower:
        return "High risk level"
    if "medium" in text_lower or "moderate" in text_lower:
        return "Medium risk level"
    if "low" in text_lower:
        return "Low risk level"
    return ""


def _label_from_percent(percent: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)", percent)
    if not m:
        return ""

    value = float(m.group(1))
    if value >= 70:
        return "High risk level"
    if value >= 31:
        return "Medium risk level"
    return "Low risk level"


def _extract_risk(soup: BeautifulSoup, text: str) -> tuple[str, str]:
    risk_el = soup.select_one(".report-info .risk-level, #report-info .risk-level, .risk-level")
    if not risk_el:
        for item in _iter_report_info_paragraphs(soup):
            item_text = item.get_text(" ", strip=True).lower()
            if item_text.startswith("risk level:"):
                risk_el = item
                break

    if risk_el:
        spans = risk_el.find_all("span")
        span_texts = [html.unescape(span.get_text(" ", strip=True)).strip() for span in spans]
        full_text = html.unescape(risk_el.get_text(" ", strip=True)).strip()

        risk_percent = ""
        risk_label = ""
        for span_text in span_texts:
            risk_percent = _extract_percent(span_text)
            if risk_percent:
                break
        if not risk_percent:
            risk_percent = _extract_percent(full_text)

        for span_text in span_texts:
            if not _extract_percent(span_text):
                risk_label = _label_from_risk_text(span_text)
                if risk_label:
                    break
        if not risk_label:
            risk_label = _label_from_risk_text(full_text) or _label_from_percent(risk_percent)

        return risk_percent, risk_label

    risk_match = re.search(
        r"Risk level:\s*<span[^>]*>\s*([^<]+?)\s*</span>\s*\((.*?)\)",
        text,
        re.I | re.S,
    )
    if not risk_match:
        return "", ""

    risk_percent = _extract_percent(html.unescape(risk_match.group(1)))
    risk_label = re.sub(r"<.*?>", "", risk_match.group(2), flags=re.S).strip()
    risk_label = _label_from_risk_text(html.unescape(risk_label)) or _label_from_percent(
        risk_percent
    )
    return risk_percent, risk_label


def parse_report_preview(
    preview_html: str, amlcheckup: str, *, base_url: str, lang: str
) -> dict[str, Any]:
    soup = BeautifulSoup(preview_html, "html.parser")
    try:
        return _parse_report_preview_soup(
            soup,
            preview_html,
            amlcheckup,
            base_url=base_url,
            lang=lang,
        )
    finally:
        _dispose_soup(soup)


def _parse_report_preview_soup(
    soup: BeautifulSoup,
    preview_html: str,
    amlcheckup: str,
    *,
    base_url: str,
    lang: str,
) -> dict[str, Any]:

    info_block = soup.select_one("#report-info, .report-info")
    text = str(info_block) if info_block else preview_html

    report_date = _extract_report_value(soup, text, "Report date")
    aml_provider = _extract_report_value(soup, text, "AML Provider")
    blockchain = _extract_report_value(soup, text, "Blockchain")
    token = _extract_report_value(soup, text, "Token")
    type_value = _extract_report_value(soup, text, "Type")
    hash_value = _extract_report_value(soup, text, "Hash")
    counterparty = _extract_report_value(soup, text, "Counterparty")

    risk_percent, risk_label = _extract_risk(soup, text)

    formatted_date = report_date or ""
    if report_date:
        try:
            dt = datetime.strptime(report_date, "%Y-%m-%d %H:%M (UTC)")
            dt = dt + timedelta(hours=5)
            formatted_date = dt.strftime("%d.%m.%Y %H:%M")
        except ValueError:
            pass

    asset_name_parts: list[str] = []
    if blockchain and blockchain.strip():
        asset_name_parts.append(blockchain.strip())
    if token and token.strip():
        asset_name_parts.append(token.strip())
    asset_name = " ".join(asset_name_parts).strip() or (token or "Адрес")

    risk_emoji = "🟢"
    risk_label_ru = "Низкий уровень риска"
    risk_label_lower = risk_label.lower()

    if "high" in risk_label_lower or "critical" in risk_label_lower:
        risk_emoji = "🔴"
        risk_label_ru = "Высокий уровень риска"
    elif "medium" in risk_label_lower or "moderate" in risk_label_lower:
        risk_emoji = "🟡"
        risk_label_ru = "Средний уровень риска"

    def parse_source_group(title_en: str) -> list[str]:
        groups = soup.select(".report-source-list .source, .cristal-item.source")

        for group in groups:
            title_el = group.select_one(".source-title, .block-title")
            if not title_el:
                continue

            group_title = title_el.get_text(" ", strip=True).lower()
            if title_en.lower() not in group_title:
                continue

            items: list[str] = []
            for li in group.select(".source-list .item"):
                item_text = re.sub(r"\s+", " ", li.get_text(" ", strip=True)).strip()
                # В отчёте по транзакции за долей следует сумма, например
                # "62.93% (~28314.03 USD)"; процент не обязан завершать строку.
                percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", item_text)
                if not percent_match:
                    continue
                if float(percent_match.group(1).replace(",", ".")) == 0:
                    continue
                item_text = item_text.replace("P2p", "P2P").replace("Atm", "ATM")
                items.append(item_text)

            return items

        return []

    trusted_sources = parse_source_group("Trusted sources")
    suspicious_sources = parse_source_group("Suspicious sources")
    dangerous_sources = parse_source_group("Dangerous sources")

    preview_link = f"{base_url}/{lang}/report-preview/{amlcheckup}"

    if not hash_value or not risk_percent:
        raise ValueError(
            "Не удалось распарсить отчет GetBlock: в HTML не найдены Hash или Risk level"
        )

    return {
        "asset_name": asset_name,
        "blockchain": blockchain or "",
        "token": token or "",
        "type": type_value or "",
        "hash": hash_value or "",
        "risk_percent": risk_percent or "",
        "risk_label": risk_label or "",
        "counterparty": counterparty or "unknown",
        "report_date": formatted_date,
        "aml_provider": aml_provider or "",
        "preview_link": preview_link,
        "risk_emoji": risk_emoji,
        "risk_label_ru": risk_label_ru,
        "trusted_sources": trusted_sources,
        "suspicious_sources": suspicious_sources,
        "dangerous_sources": dangerous_sources,
    }


def build_report_message(report_data: dict[str, Any]) -> str:
    asset_name = report_data.get("asset_name", "Адрес")
    hash_value = report_data.get("hash", "")
    risk_emoji = report_data.get("risk_emoji", "🟢")
    risk_label_ru = report_data.get("risk_label_ru", "Низкий уровень риска")
    risk_percent = report_data.get("risk_percent", "")
    counterparty = report_data.get("counterparty", "unknown")
    report_date = report_data.get("report_date", "")
    preview_link = report_data.get("preview_link", "")

    trusted_sources = report_data.get("trusted_sources", [])
    suspicious_sources = report_data.get("suspicious_sources", [])
    dangerous_sources = report_data.get("dangerous_sources", [])

    target_label = "Транзакция" if str(report_data.get("type", "")).lower() == "transaction" else "Адрес"
    parts = [
        f"{asset_name} {target_label}:",
        f"{hash_value}",
        "",
        f"{risk_emoji} {risk_label_ru}: {risk_percent}",
        f"📅 Дата AML проверки: {report_date}",
        f"Контрагент: {counterparty}",
        "",
    ]

    if trusted_sources:
        parts.append("✅ Доверенные источники")
        parts.extend(trusted_sources)
        parts.append("")

    if suspicious_sources:
        parts.append("⚠️ Подозрительные источники")
        parts.extend(suspicious_sources)
        parts.append("")

    if dangerous_sources:
        parts.append("🛑 Опасные источники")
        parts.extend(dangerous_sources)
        parts.append("")

    parts.extend(["Полный отчет:", preview_link])
    return "\n".join(parts)
