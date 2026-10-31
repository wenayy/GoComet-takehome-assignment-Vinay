"""Money and date arithmetic, exactly as GLOSSARY.md states it: Decimal, half up to cents, nothing else rounded."""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def D(x) -> Decimal | None:
    """A number however a model wrote it: 23.54, "23.54", "23.54%", "USD 1,234.50", "INR 90,535.08"."""
    if x is None or x == "" or isinstance(x, bool):
        return None
    if isinstance(x, Decimal):
        return x
    if isinstance(x, (int, float)):
        return Decimal(str(x))
    m = _NUM.search(str(x).replace(",", ""))
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


def r2(x) -> Decimal:
    """Round half up (ties away from zero) to cents."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def pct_of(pct, base) -> Decimal:
    return r2(D(pct) / Decimal(100) * D(base))


def money(x) -> str | None:
    return None if x is None else f"{r2(x):.2f}"


def to_date(s) -> date | None:
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def days(a, b) -> int:
    """b - a in days."""
    return (to_date(b) - to_date(a)).days


def add_days(d, n: int) -> date:
    return to_date(d) + timedelta(days=n)


def ceil_div(a: int, b: int) -> int:
    return math.ceil(a / b)


def quarter_of(d) -> str:
    d = to_date(d)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def quarter_bounds(q: str) -> tuple[date, date]:
    y, n = int(q[:4]), int(q[-1])
    start = date(y, 3 * (n - 1) + 1, 1)
    end = date(y + (n == 4), (3 * n) % 12 + 1, 1) - timedelta(days=1)
    return start, end


def add_years(d, n: int) -> date:
    d = to_date(d)
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + n, day=28)
