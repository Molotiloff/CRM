from domain import CurrencyCode


class Currency:
    def __init__(self, code, precision=2):
        if precision < 0 or precision > 8:
            raise ValueError("precision должен быть в диапазоне 0..8")

        self.code = str(CurrencyCode(code))
        self.precision = precision

    def __repr__(self):
        return f"Currency(code={self.code!r} precision={self.precision!r})"
