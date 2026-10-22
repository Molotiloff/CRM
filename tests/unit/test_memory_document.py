from telegram_adapters.memory_document import EphemeralBufferedInputFile


async def test_upload_stream_explicitly_closes_its_buffer_on_completion():
    document = EphemeralBufferedInputFile(b"test-pdf", filename="report.pdf")
    assert b"".join([chunk async for chunk in document.read(None)]) == b"test-pdf"
    assert not document._buffers
    document.release()
    assert document.data == b""


async def test_release_frees_buffers_even_if_cancelled_http_retains_generator():
    document = EphemeralBufferedInputFile(b"x" * 200_000, filename="report.pdf")
    stream = document.read(None)
    assert len(await anext(stream)) == document.chunk_size
    buffers = list(document._buffers)
    document.release()
    assert document.data == b""
    assert all(buffer.closed for buffer in buffers)
    assert not document._buffers
    await stream.aclose()
