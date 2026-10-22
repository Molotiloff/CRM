from __future__ import annotations

from collections.abc import AsyncGenerator
from io import BytesIO

from aiogram import Bot
from aiogram.types import BufferedInputFile


class EphemeralBufferedInputFile(BufferedInputFile):
    """An upload whose buffers can be freed even if the HTTP method is retained.

    Unlike BufferedInputFile.read, stream buffers are explicitly closed. The
    owner must call release() in finally, after send_document finishes or fails.
    """

    def __init__(self, data: bytes, *, filename: str) -> None:
        super().__init__(data, filename=filename)
        self._buffers: set[BytesIO] = set()

    async def read(self, bot: Bot) -> AsyncGenerator[bytes, None]:
        with BytesIO(self.data) as buffer:
            self._buffers.add(buffer)
            try:
                while chunk := buffer.read(self.chunk_size):
                    yield chunk
            finally:
                self._buffers.discard(buffer)

    def release(self) -> None:
        self.data = b""
        for buffer in self._buffers:
            buffer.close()
        self._buffers.clear()
