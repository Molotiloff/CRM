# models/wallet.py

from decimal import ROUND_HALF_UP, Decimal, getcontext

from models.currency import Currency

getcontext().prec = 28


class WalletError(Exception):
    pass


class Wallet:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self._currencies = {}
        self._balances = {}

    def ensure_currency(self, code):
        code = code.strip().upper()
        if code not in self._currencies:
            raise WalletError(f"Валюта {code} не найдена в кошельке")

    def add_currency(self, currency: Currency) -> None:
        """
        Новый счёт (валюту) в кошелёк.
        Ошибка, если уже есть счёт с таким кодом (без учёта регистра).
        """
        code = (currency.code or "").strip().upper()
        if not code:
            raise WalletError("Код валюты не должен быть пустым")

        precision = int(currency.precision)
        if not (0 <= precision <= 8):
            raise WalletError("Точность должна быть в диапазоне 0..8")

        if code in self._currencies:
            raise WalletError("Счёт с таким кодом уже существует!")

        # создаём валюту (без name)
        self._currencies[code] = Currency(code=code, precision=precision)
        self._balances.setdefault(code, Decimal("0"))

    def list_currencies(self):
        return [self._currencies[c] for c in sorted(self._currencies.keys())]

    def _quantize(self, code, amount):
        cur = self._currencies[code]
        q = Decimal(10) ** -cur.precision
        return amount.quantize(q, rounding=ROUND_HALF_UP)

    def get_balance(self, code):
        code = code.strip().upper()
        self.ensure_currency(code)
        return self._quantize(code, self._balances.get(code, Decimal("0")))

    def get_currency(self, code: str) -> Currency:
        code = code.strip().upper()
        self.ensure_currency(code)
        return self._currencies[code]

    def deposit(self, code, amount):
        code = code.strip().upper()
        self.ensure_currency(code)
        if amount <= 0:
            raise WalletError("Сумма пополнения должна быть > 0")
        self._balances[code] = self._balances.get(code, Decimal("0")) + amount
        self._balances[code] = self._quantize(code, self._balances[code])
        return self._balances[code]

    def withdraw(self, code: str, amount: Decimal) -> Decimal:
        code = code.strip().upper()
        self.ensure_currency(code)
        if amount <= 0:
            raise WalletError("Сумма списания должна быть > 0")
        new_val = self._balances.get(code, Decimal("0")) - amount
        self._balances[code] = self._quantize(code, new_val)
        return self._balances[code]

    def set_balance(self, code: str, amount: Decimal) -> None:
        code = code.strip().upper()
        self.ensure_currency(code)
        self._balances[code] = self._quantize(code, amount)

    def snapshot(self):

        out = []
        for code in sorted(self._currencies.keys()):
            bal = self.get_balance(code)
            out.append((code, bal))
        return out

    def remove_currency(self, code: str, *, allow_nonzero: bool = False) -> None:
        """
        Удаляет счёт из кошелька.
        По умолчанию — только при нулевом балансе.
        Если allow_nonzero=True — удаляет независимо от остатка.
        """
        code_u = code.strip().upper()

        if code_u not in self._currencies:
            raise WalletError(f"Счёт {code_u} не найден")

        bal = self._balances.get(code_u, Decimal("0"))

        if (bal != 0) and not allow_nonzero:
            # прежняя логика: запрет, если баланс не нулевой
            raise WalletError("Нельзя удалить счёт с ненулевым балансом.")

        # фактическое удаление
        self._currencies.pop(code_u, None)
        self._balances.pop(code_u, None)
