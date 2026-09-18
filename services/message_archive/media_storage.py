from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class StoredMedia:
    key: str
    sha256: str
    size: int


class MediaStoragePort(Protocol):
    async def put(self, *, key: str, chunks: AsyncIterator[bytes]) -> StoredMedia: ...

    async def exists(self, *, key: str) -> bool: ...

    def open(self, *, key: str) -> AbstractAsyncContextManager[BinaryIO]: ...


class LocalMediaStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def _resolve(self, key: str) -> Path:
        if not key or Path(key).is_absolute():
            raise ValueError("Storage key must be a non-empty relative path")
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Storage key escapes media root")
        return target

    async def put(self, *, key: str, chunks: AsyncIterator[bytes]) -> StoredMedia:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("wb") as output:
                temporary.chmod(0o600)
                async for chunk in chunks:
                    if not chunk:
                        continue
                    await asyncio.to_thread(output.write, chunk)
                    digest.update(chunk)
                    size += len(chunk)
                await asyncio.to_thread(self._flush, output)
            await asyncio.to_thread(os.replace, temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredMedia(key=key, sha256=digest.hexdigest(), size=size)

    async def exists(self, *, key: str) -> bool:
        return self._resolve(key).is_file()

    @asynccontextmanager
    async def open(self, *, key: str) -> AsyncIterator[BufferedReader]:
        path = self._resolve(key)
        if not path.is_file():
            raise FileNotFoundError(path)
        source = path.open("rb")
        try:
            yield source
        finally:
            source.close()

    @staticmethod
    def _flush(output: BinaryIO) -> None:
        output.flush()
        os.fsync(output.fileno())

