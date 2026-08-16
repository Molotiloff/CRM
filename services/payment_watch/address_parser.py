from __future__ import annotations

import re

from domain.tron_wallet import is_probable_tron_wallet, normalize_wallet

_TRON_ADDRESS_RE = re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}")


def extract_tron_address(*candidates: str | None) -> str | None:
    for candidate in candidates:
        raw = candidate or ""
        match = _TRON_ADDRESS_RE.search(raw)
        if not match:
            continue
        wallet = normalize_wallet(match.group(0))
        if is_probable_tron_wallet(wallet):
            return wallet
    return None
