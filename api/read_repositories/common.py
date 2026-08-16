def clean_filter(value: str | None) -> str | None:
    raw = (value or "").strip()
    return raw or None
