from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

# Участник: @telegram или +телефон (6–15 цифр)
_PART_RE = re.compile(r"^(?:@[A-Za-z0-9_]{2,}|\+\d{6,15})$")


@dataclass(frozen=True, slots=True)
class ParsedRequest:
    cmd: str
    kind: str  # "dep" | "wd" | "fx"
    city: str

    # dep/wd
    amount_expr: str = ""
    code: str = ""

    # fx
    in_code: str = ""
    out_code: str = ""
    amt_in_expr: str = ""
    amt_out_expr: str = ""

    # common
    contact1: str = ""  # dep/fx: кассир (наш контакт); wd: выдаёт (семантика задаётся отдельно)
    contact2: str = ""  # клиент/второй контакт (опционально)
    comment: str = ""


def _split_comment(tail: str) -> tuple[str, str]:
    before = (tail or "").strip()
    if "!" not in before:
        return before, ""
    a, b = before.split("!", 1)
    return a.strip(), b.strip()


def _pick_city(
    tokens: list[str], *, city_keys: set[str], default_city: str
) -> tuple[str, list[str]]:
    city = (default_city or "екб").strip().lower()
    if tokens and tokens[0].strip().lower() in city_keys:
        city = tokens[0].strip().lower()
        tokens = tokens[1:]
    return city, tokens


def _pick_contacts(tokens: list[str]) -> tuple[str, str, list[str]]:
    """
    Снимаем контакты с конца (0/1/2).
    Возвращает (contact1, contact2, rest_tokens_without_contacts)

    contact1 = "первый" контакт (кассир/выдаёт по семантике обработчика)
    contact2 = "второй" контакт (опционально)
    """
    if not tokens:
        return "", "", tokens

    last = tokens[-1].strip()
    if not _PART_RE.match(last):
        return "", "", tokens

    if len(tokens) >= 2:
        prev = tokens[-2].strip()
        if _PART_RE.match(prev):
            return prev, last, tokens[:-2]

    return last, "", tokens[:-1]


def parse_dep_wd(
    raw_text: str,
    *,
    cmd_map: Mapping[str, tuple[str, str]],  # CMD_MAP
    city_keys: set[str],
    default_city: str,
) -> ParsedRequest | None:
    """
    Новое:
      /депр [город] <amount_expr> [contact1] [contact2] [! comment]
      /выдр [город] <amount_expr> [contact1] [contact2] [! comment]

    Контакты НЕОБЯЗАТЕЛЬНЫ:
      - 0 контактов: всё после (возможного) города -> amount_expr
      - 1 контакт: последний токен контакт, остальное -> amount_expr
      - 2 контакта: два последних токена контакты, остальное -> amount_expr
    """
    text = (raw_text or "").strip()
    if not text.startswith("/"):
        return None

    first, *rest = text.split(maxsplit=1)
    cmd = first[1:].split("@", 1)[0].lower()
    if cmd not in cmd_map:
        return None

    tail = rest[0] if rest else ""
    if not tail.strip():
        return None

    before_comment, comment = _split_comment(tail)
    tokens = before_comment.split()
    if not tokens:
        return None

    city, tokens = _pick_city(tokens, city_keys=city_keys, default_city=default_city)
    if not tokens:
        return None

    contact1, contact2, core = _pick_contacts(tokens)

    amount_expr = " ".join(core).strip()
    if not amount_expr:
        return None

    kind, code = cmd_map[cmd]
    return ParsedRequest(
        cmd=cmd,
        kind=kind,
        city=city,
        amount_expr=amount_expr,
        code=str(code).upper(),
        contact1=contact1,
        contact2=contact2,
        comment=comment,
    )


def parse_fx(
    raw_text: str,
    *,
    fx_cmd_map: Mapping[str, tuple[str, str, str]],
    city_keys: set[str],
    default_city: str,
) -> ParsedRequest | None:
    """
    Новое:
      /првд [город] <amt_in_expr> <amt_out_expr> [contact1] [contact2] [! comment]

    Контакты НЕОБЯЗАТЕЛЬНЫ (0/1/2).
    Город опционален.
    """

    text = (raw_text or "").strip()
    if not text.startswith("/"):
        return None

    first, *rest = text.split(maxsplit=1)
    cmd = first[1:].split("@", 1)[0].lower()
    if cmd not in fx_cmd_map:
        return None

    tail = rest[0] if rest else ""
    if not tail.strip():
        return None

    before_comment, comment = _split_comment(tail)
    tokens = before_comment.split()
    if not tokens:
        return None

    city, tokens = _pick_city(tokens, city_keys=city_keys, default_city=default_city)

    # минимум должны остаться 2 токена (amt_in, amt_out)
    if len(tokens) < 2:
        return None

    # снимаем 0/1/2 контакта с конца
    contact1, contact2, core = _pick_contacts(tokens)

    # после удаления контактов должно быть ровно 2 токена
    if len(core) != 2:
        return None

    amt_in_expr = core[0].strip()
    amt_out_expr = core[1].strip()
    if not amt_in_expr or not amt_out_expr:
        return None

    kind, in_code, out_code = fx_cmd_map[cmd]
    return ParsedRequest(
        cmd=cmd,
        kind=kind,
        city=city,
        in_code=str(in_code).upper(),
        out_code=str(out_code).upper(),
        amt_in_expr=amt_in_expr,
        amt_out_expr=amt_out_expr,
        contact1=contact1,
        contact2=contact2,
        comment=comment,
    )
