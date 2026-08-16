from __future__ import annotations

CB_ISSUE_DONE = "req:issue_done"
CB_DEAL_DONE = "cash:deal_done:req:"
CB_DEAL_CANCEL = "cash:deal_cancel:req:"

CMD_MAP: dict[str, tuple[str, str]] = {
    "депр": ("dep", "RUB"),
    "депт": ("dep", "USDT"),
    "депд": ("dep", "USD"),
    "депе": ("dep", "EUR"),
    "депб": ("dep", "USDW"),
    "выдр": ("wd", "RUB"),
    "выдт": ("wd", "USDT"),
    "выдд": ("wd", "USD"),
    "выде": ("wd", "EUR"),
    "выдб": ("wd", "USDW"),
}

FX_CMD_MAP: dict[str, tuple[str, str, str]] = {
    "првд": ("fx", "RUB", "USD"),
    "пдвр": ("fx", "USD", "RUB"),
    "прве": ("fx", "RUB", "EUR"),
    "певр": ("fx", "EUR", "RUB"),
    "првб": ("fx", "RUB", "USDW"),
    "пбвр": ("fx", "USDW", "RUB"),
    "првп": ("fx", "RUB", "EUR500"),
    "ппвр": ("fx", "EUR500", "RUB"),
    "пдве": ("fx", "USD", "EUR"),
    "певд": ("fx", "EUR", "USD"),
    "пдвб": ("fx", "USD", "USDW"),
    "пбвд": ("fx", "USDW", "USD"),
    "пдвп": ("fx", "USD", "EUR500"),
    "ппвд": ("fx", "EUR500", "USD"),
    "певб": ("fx", "EUR", "USDW"),
    "пбве": ("fx", "USDW", "EUR"),
    "певп": ("fx", "EUR", "EUR500"),
    "ппве": ("fx", "EUR500", "EUR"),
    "пбвп": ("fx", "USDW", "EUR500"),
    "ппвб": ("fx", "EUR500", "USDW"),
}
