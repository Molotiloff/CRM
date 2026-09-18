from __future__ import annotations

import asyncio
import hashlib

import pytest

from services.message_archive.media_storage import LocalMediaStorage


async def _chunks():
    yield b"hello"
    yield b" "
    yield b"world"


def test_local_storage_writes_atomically_and_hashes(tmp_path) -> None:
    storage = LocalMediaStorage(tmp_path)

    result = asyncio.run(storage.put(key="1/2/voice.ogg", chunks=_chunks()))

    assert result.size == 11
    assert result.sha256 == hashlib.sha256(b"hello world").hexdigest()
    assert asyncio.run(storage.exists(key=result.key))

    async def read_back() -> bytes:
        async with storage.open(key=result.key) as source:
            return source.read()

    assert asyncio.run(read_back()) == b"hello world"


@pytest.mark.parametrize("key", ["../escape", "/absolute/path", ""])
def test_local_storage_rejects_unsafe_keys(tmp_path, key: str) -> None:
    storage = LocalMediaStorage(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(storage.exists(key=key))

