from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class GetBlockSettings:
    identity: str
    password: str
    lang: str
    user_id: str
    currency_code: str
    token_id: str
    aml_provider: str
    direction: str
    source: str
    type_: str
    reports_dir: str
