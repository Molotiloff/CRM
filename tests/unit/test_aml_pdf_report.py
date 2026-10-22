from __future__ import annotations

import builtins
import gc
import io
import weakref

import pytest
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from services.aml import pdf_report
from services.aml.models import AMLCheckRequest
from services.aml.pdf_report import AMLReportPDFBuilder


def report(**overrides):
    return {
        "hash": "TPixuHv6cPScVCuCAyzTygHLpgUkp8vUcZ", "asset_name": "TRX USDT", "token": "USDT",
        "risk_percent": "50.4796%", "aml_provider": "BitOK", "report_date": "13.10.2026 18:51",
        "counterparty": "Unknown", "preview_link": "https://getblock.net/en/report-preview/check-id",
        "trusted_sources": ["Exchange Licensed 77.07%", "Other 14.06%", "Payment 1.04%"],
        "suspicious_sources": ["Liquidity Pools 4.22%", "Exchange Unlicensed 0.69%"],
        "dangerous_sources": ["Enforcement Action 1.94%", "Exchange Sanctioned EU 0.61%", "Gambling 0.3%",
                              "Sanctions 0.05%", "Mixer 0.02%", "Illegal Service 0.01%", "Stolen Coins 0%", "Scam 0%", "Terrorism Financing 0%"],
        **overrides,
    }


def read_pdf(data):
    # The reader is test-only; the application never reopens or writes its PDF.
    return PdfReader(io.BytesIO(data))


def test_a4_template_contains_real_report_and_clickable_links():
    data = AMLReportPDFBuilder().build(report(), request=AMLCheckRequest("Taddress"))
    assert data.startswith(b"%PDF-")
    reader = read_pdf(data)
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(595.28, abs=0.1)
    assert float(page.mediabox.height) == pytest.approx(841.89, abs=0.1)
    text = page.extract_text()
    for value in ("50.4796%", "СРЕДНИЙ РИСК", "TPixuHv6cPScVCuCAyzTygHLpgUkp8vUcZ", "Terrorism Financing", "BitOK", "UTC+5"):
        assert value in text
    links = [item.get_object()["/A"]["/URI"] for item in page["/Annots"]]
    assert report()["preview_link"] in links
    assert "https://sky-ex.ru" in links


@pytest.mark.parametrize(("percent", "label"), [("0%", "НИЗКИЙ РИСК"), ("31%", "СРЕДНИЙ РИСК"), ("70%", "ВЫСОКИЙ РИСК"), ("100%", "ВЫСОКИЙ РИСК"), ("", "НЕТ ОЦЕНКИ")])
def test_risk_categories_and_missing_risk(percent, label):
    data = AMLReportPDFBuilder().build(report(risk_percent=percent), request=AMLCheckRequest("wallet"))
    assert label in read_pdf(data).pages[0].extract_text()


@pytest.mark.parametrize("network", ["trc20", "erc20", "bep20", "btc"])
def test_all_address_networks_include_wallet_balance_if_available(network):
    data = AMLReportPDFBuilder().build(report(), request=AMLCheckRequest("address", network=network), wallet_balance="123.456789 USDT")
    text = read_pdf(data).pages[0].extract_text()
    assert "123.456789 USDT" in text
    assert "Остаток USDT" in text


def test_transaction_hash_and_all_sources_survive_multipage_layout():
    sources = [f"UniqueSource{i:03} Очень длинное название 12.34% (~28314.03 USD)" for i in range(45)]
    data = AMLReportPDFBuilder().build(report(hash="a" * 64, dangerous_sources=sources), request=AMLCheckRequest("a" * 64, kind="transaction"))
    reader = read_pdf(data)
    assert len(reader.pages) > 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Хэш транзакции" in text
    assert "a" * 64 in text.replace("\n", "")
    for i in range(45):
        assert f"UniqueSource{i:03}" in text
    assert text.count("28314.03 USD") == 45


def test_builder_closes_output_and_does_not_retain_canvases_or_font_registrations(monkeypatch):
    buffers = []
    canvases = []
    original_canvas = pdf_report.Canvas

    def buffer():
        value = io.BytesIO()
        buffers.append(value)
        return value

    def canvas(*args, **kwargs):
        value = original_canvas(*args, **kwargs)
        canvases.append(weakref.ref(value))
        return value

    monkeypatch.setattr(pdf_report, "BytesIO", buffer)
    monkeypatch.setattr(pdf_report, "Canvas", canvas)
    builder = AMLReportPDFBuilder()
    builder.build(report(), request=AMLCheckRequest("wallet"))
    registered = set(pdfmetrics.getRegisteredFontNames())
    for _ in range(12):
        builder.build(report(), request=AMLCheckRequest("wallet"))
    gc.collect()
    assert all(value.closed for value in buffers)
    assert all(reference() is None for reference in canvases)
    assert set(pdfmetrics.getRegisteredFontNames()) == registered


def test_buffer_closes_on_pdf_generation_failure(monkeypatch):
    output = io.BytesIO()
    monkeypatch.setattr(pdf_report, "BytesIO", lambda: output)
    monkeypatch.setattr(pdf_report._ReportPage, "draw", lambda self: (_ for _ in ()).throw(RuntimeError("render failed")))
    with pytest.raises(RuntimeError, match="render failed"):
        AMLReportPDFBuilder().build(report(), request=AMLCheckRequest("wallet"))
    assert output.closed


def test_renderer_never_opens_a_file_for_writing(monkeypatch):
    original = builtins.open

    def read_only(file, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+"), (file, mode)
        return original(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", read_only)
    assert AMLReportPDFBuilder().build(report(), request=AMLCheckRequest("wallet")).startswith(b"%PDF-")
