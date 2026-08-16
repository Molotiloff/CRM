from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, localcontext


class CalcError(Exception):
    pass


# Суффикс «к»/«k» после числа = ×1000 за каждую букву (кириллица и латиница,
# оба регистра). 40к → 40000, 40кк → 40 000 000, 40,5к → 40500, 20к/77 → 20000/77.
_K_SUFFIX_RE = re.compile(r"(\d+(?:\.\d+)?|\.\d+)([kKкК]+)")


def expand_k_suffix(s: str) -> str:
    """Развернуть «к»/«k»-суффикс после числа в ×1000 за букву.

    Чистое строковое преобразование: трогает только `цифры[.цифры]` сразу
    перед одной/несколькими `к/k`, всё прочее (буквы, операторы, %) — без
    изменений. Поэтому безопасно для текстов с кодами валют (AED 30k → AED 30000).
    """
    def _repl(m: re.Match[str]) -> str:
        with localcontext() as context:
            context.prec = 28
            value = Decimal(m.group(1)) * (Decimal(1000) ** len(m.group(2)))
        return f"{value:f}"

    return _K_SUFFIX_RE.sub(_repl, s)


def _tokenize(s):
    s = s.strip().replace(",", ".")
    if not s:
        raise CalcError("Пустое выражение")
    s = expand_k_suffix(s)
    allowed = set("0123456789.+-*/()% ")
    if any(ch not in allowed for ch in s):
        raise CalcError("Недопустимый символ в выражении")
    tokens = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "+-*/()":
            tokens.append(ch)
            i += 1
            continue
        if ch == '%':
            tokens.append('%')
            i += 1
            continue
        if ch.isdigit() or ch == '.':
            j = i
            dot = 0
            while j < n and (s[j].isdigit() or s[j] == '.'):
                if s[j] == '.':
                    dot += 1
                    if dot > 1:
                        raise CalcError("Некорректное число")
                j += 1
            num = s[i:j]
            if num == '.':
                raise CalcError("Некорректное число")
            tokens.append(num)
            i = j
            continue
        raise CalcError("Некорректный символ")
    return tokens


class _Percent:
    __slots__ = ("value",)

    def __init__(self, value: Decimal):
        self.value = value


def _parse(tokens):
    idx = [0]  # обёртка для "ссылки" на индекс

    def cur():
        return tokens[idx[0]] if idx[0] < len(tokens) else None

    def eat(x=None):
        tok = cur()
        if tok is None:
            return None
        if x is None or tok == x:
            idx[0] += 1
            return tok
        return None

    def parse_expr():
        left = parse_term()
        while cur() in ('+', '-'):
            op = eat()
            right = parse_term()
            # Особая семантика процентов в +/-
            if isinstance(right, _Percent):
                right = left * (right.value / Decimal(100))
            left = left + right if op == '+' else left - right
        return left

    def parse_term():
        left = parse_factor()
        while cur() in ('*', '/'):
            op = eat()
            right = parse_factor()
            # В * и / процент — это просто доля (y/100)
            if isinstance(right, _Percent):
                right = right.value / Decimal(100)
            if op == '*':
                left = left * right
            else:
                if right == 0:
                    raise CalcError("Деление на ноль")
                left = left / right
        return left

    def parse_factor():
        # унарные +/-
        if cur() in ('+', '-'):
            sign = eat()
            val = parse_factor()
            return val if sign == '+' else (Decimal(0) - val)
        if eat('('):
            val = parse_expr()
            if not eat(')'):
                raise CalcError("Несбалансированные скобки")
            # суффиксный % после скобок
            if cur() == '%':
                eat('%')
                return _Percent(val)
            return val
        tok = cur()
        if tok is None:
            raise CalcError("Ожидалось число")
        # число
        if tok not in ('+', '-', '*', '/', '(', ')', '%'):
            eat()  # съедаем число
            try:
                val = Decimal(tok)
            except InvalidOperation:
                raise CalcError("Некорректное число") from None
            # суффиксный %
            if cur() == '%':
                eat('%')
                return _Percent(val)
            return val
        raise CalcError("Ожидалось число")

    val = parse_expr()
    if cur() is not None:
        raise CalcError("Лишние символы в выражении")
    return val


def evaluate(expression: str) -> Decimal:
    tokens = _tokenize(expression)
    with localcontext() as context:
        context.prec = 28
        result = _parse(tokens)
        if isinstance(result, _Percent):
            return result.value / Decimal(100)
        return result
