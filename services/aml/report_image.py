"""Render one report page at a time, without keeping native PDF objects alive."""
from __future__ import annotations

import threading
from io import BytesIO

import pypdfium2 as pdfium

# PDFium is not thread-safe. Keep all native operations under one process lock,
# even if more than one AML handler/executor is instantiated.
_PDFIUM_LOCK = threading.Lock()
# The interpretation card ends 720 pt below the top of the A4 template.
# Keep 12 pt of bottom padding, excluding the footer, links and QR code.
_IMAGE_BOTTOM = 732.0


def render_report_page(pdf_data: bytes, page_index: int) -> tuple[bytes, int]:
    with _PDFIUM_LOCK:
        document = pdfium.PdfDocument(pdf_data)
        try:
            page_count = len(document)
            page = document[page_index]
            try:
                height = page.get_height()
                bitmap = page.render(
                    scale=1800 / height,
                    crop=(0, max(0.0, height - _IMAGE_BOTTOM), 0, 0),
                )
                try:
                    image = bitmap.to_pil()
                    try:
                        with BytesIO() as output:
                            image.save(output, format="PNG")
                            return output.getvalue(), page_count
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
