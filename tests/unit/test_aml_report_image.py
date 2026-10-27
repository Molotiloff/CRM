import io
from unittest.mock import patch

import pytest
from PIL import Image

from services.aml.models import AMLCheckRequest
from services.aml.pdf_report import AMLReportPDFBuilder
from services.aml.report_image import render_report_page


def test_pdf_page_renders_to_readable_png_in_memory():
    pdf = AMLReportPDFBuilder().build({"risk_percent": "51.2449%"}, request=AMLCheckRequest("Taddress"))
    data, count = render_report_page(pdf, 0)
    assert count == 1
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        # Keep the interpretation card, but not the footer below it.
        assert 1565 <= image.height <= 1566
        assert 1200 < image.width < 1300


def test_all_pages_are_cropped_after_interpretation_without_changing_content(monkeypatch):
    from services.aml import report_image

    pdf = AMLReportPDFBuilder().build(
        {"dangerous_sources": [f"Source{i} 10%" for i in range(40)]},
        request=AMLCheckRequest("Taddress"), wallet_balance="123.456789 USDT",
    )
    original = report_image.pdfium.PdfPage.render
    crops = []

    def render(page, *args, **kwargs):
        crops.append(kwargs["crop"])
        return original(page, *args, **kwargs)

    monkeypatch.setattr(report_image.pdfium.PdfPage, "render", render)
    _, count = render_report_page(pdf, 0)
    assert count > 1
    for index in range(1, count):
        data, pages = render_report_page(pdf, index)
        assert pages == count
        with Image.open(io.BytesIO(data)) as image:
            assert 1565 <= image.height <= 1566
    assert len(crops) == count
    for left, bottom, right, top in crops:
        assert (left, right, top) == (0, 0, 0)
        assert bottom == pytest.approx(841.89 - 732, abs=0.1)


def test_pdfium_objects_close_even_when_png_encoding_fails():
    pdf = AMLReportPDFBuilder().build({}, request=AMLCheckRequest("Taddress"))
    from services.aml import report_image

    original = report_image.pdfium.PdfDocument
    captured = []

    def capture(data):
        document = original(data)
        captured.append(document)
        return document

    with patch.object(report_image.pdfium, "PdfDocument", side_effect=capture), patch.object(Image.Image, "save", side_effect=RuntimeError("encoding failed")):
        with pytest.raises(RuntimeError, match="encoding failed"):
            render_report_page(pdf, 0)
    assert captured[0].raw is None
