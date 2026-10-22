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
        # PDFium rounds the floating-point A4 dimensions up to whole pixels.
        assert 1800 <= image.height <= 1801
        assert 1200 < image.width < 1300


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
