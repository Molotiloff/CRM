from __future__ import annotations

import re

_TRON_BASE58_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


def normalize_wallet(raw: str) -> str:
    return (raw or "").strip()


def is_probable_tron_wallet(wallet: str) -> bool:
    return bool(_TRON_BASE58_RE.fullmatch(wallet))
