from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def eth_amount(value, places=6):
    """Muestra ETH sin ceros finales, conservando precisión de hasta `places`."""
    try:
        amount = Decimal(str(value or 0))
        precision = max(0, min(int(places), 18))
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    rendered = f"{amount:.{precision}f}".rstrip("0").rstrip(".")
    return (rendered or "0").replace(".", ",")


@register.filter
def position_short(value):
    return {
        "Goalkeeper": "GK",
        "Defender": "DF",
        "Midfielder": "MD",
        "Forward": "FW",
    }.get(value, "O")
