"""Safe arithmetic for post-aggregation KPIs.

NULL in either operand yields NULL. A zero denominator yields NULL, never 0
and never an invented rate. Rounding matches P4 money scale for money ratios.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# fact_campaign_day money columns are numeric(18, 4).
MONEY_SCALE = Decimal("0.0001")
# Rate KPIs (counts / counts) are stored at 6 decimal places.
RATE_SCALE = Decimal("0.000001")

ZERO = Decimal("0")


def as_decimal(value: int | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(value)


def safe_divide(
    numerator: int | Decimal | None,
    denominator: int | Decimal | None,
    *,
    scale: Decimal,
) -> Decimal | None:
    """Divide after aggregation. NULL or zero denominator → NULL."""
    num = as_decimal(numerator)
    den = as_decimal(denominator)
    if num is None or den is None:
        return None
    if den == ZERO:
        return None
    return (num / den).quantize(scale, rounding=ROUND_HALF_UP)


def safe_add(
    left: int | Decimal | None,
    right: int | Decimal | None,
    *,
    scale: Decimal | None = None,
) -> Decimal | None:
    a = as_decimal(left)
    b = as_decimal(right)
    if a is None or b is None:
        return None
    result = a + b
    if scale is None:
        return result
    return result.quantize(scale, rounding=ROUND_HALF_UP)


def safe_subtract(
    left: int | Decimal | None,
    right: int | Decimal | None,
    *,
    scale: Decimal | None = None,
) -> Decimal | None:
    a = as_decimal(left)
    b = as_decimal(right)
    if a is None or b is None:
        return None
    result = a - b
    if scale is None:
        return result
    return result.quantize(scale, rounding=ROUND_HALF_UP)
