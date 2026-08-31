from __future__ import annotations

from decimal import Decimal

from sorare_utils import fetch_exchange_rates, get_live_single_sale_offers, graphql_request, to_eur_cents


ALLOWED_RARITIES = {"limited", "rare", "super_rare", "unique"}


PREPARE_DIRECT_OFFER_QUERY = """
mutation PrepareDirectOffer($input: prepareOfferInput!) {
    prepareOffer(input: $input) {
        errors { message }
    }
}
"""

DIRECT_OFFER_ETH_RATE_QUERY = """
query DirectOfferEthRate {
    config { exchangeRate { ethRates { eurCents } } }
}
"""


def direct_offer_payment_amount(amount_cents, currency="EUR", *, headers=None):
    """Convierte un importe expresado en céntimos EUR al rail elegido por Sorare."""
    amount_cents = int(amount_cents)
    currency = str(currency or "EUR").strip().upper()
    if currency == "EUR":
        return {"amount": str(amount_cents), "currency": "EUR"}
    if currency != "ETH":
        raise ValueError("La moneda de pago debe ser EUR o ETH")
    data = graphql_request(DIRECT_OFFER_ETH_RATE_QUERY, headers=headers)
    rate_cents = int((((data.get("config") or {}).get("exchangeRate") or {}).get("ethRates") or {}).get("eurCents") or 0)
    if rate_cents <= 0:
        raise RuntimeError("Sorare no devolvió una tasa EUR/ETH válida")
    wei = max(1, amount_cents * 10**18 // rate_cents)
    return {"amount": str(wei), "currency": "WEI"}


def check_direct_offer_payment(asset_id, manager_slug, payment_amount, *, headers=None):
    settlement_currency = str(payment_amount.get("currency") or "").upper()
    if settlement_currency not in {"EUR", "WEI"}:
        raise ValueError("El importe de pago no tiene una moneda compatible")
    data = graphql_request(
        PREPARE_DIRECT_OFFER_QUERY,
        {
            "input": {
                "sendAssetIds": [],
                "receiveAssetIds": [asset_id],
                "settlementCurrencies": [settlement_currency],
                "sendAmount": payment_amount,
                "receiverSlug": manager_slug,
                "clientMutationId": f"payment-check-{asset_id}",
            }
        },
        headers=headers,
    )
    errors = (data.get("prepareOffer") or {}).get("errors") or []
    return [str(error.get("message") or "").strip() for error in errors if error.get("message")]


def check_direct_offer_eur(asset_id, manager_slug, amount_cents, *, headers=None):
    return check_direct_offer_payment(
        asset_id,
        manager_slug,
        {"amount": str(amount_cents), "currency": "EUR"},
        headers=headers,
    )


def _original_price(amounts):
    amounts = amounts or {}
    for field, currency, divisor in (
        ("eurCents", "EUR", Decimal("100")),
        ("usdCents", "USD", Decimal("100")),
        ("gbpCents", "GBP", Decimal("100")),
        ("wei", "ETH", Decimal("1000000000000000000")),
    ):
        value = amounts.get(field)
        if value is not None and str(value) != "0":
            return currency, Decimal(str(value)) / divisor
    return None, None


def player_in_season_listings(player_slug, *, headers=None, rates=None):
    rows = []
    for offer in get_live_single_sale_offers(player_slug, headers=headers):
        cards = (offer.get("senderSide") or {}).get("anyCards") or []
        card = cards[0] if cards else {}
        rarity = str(card.get("rarityTyped") or "").lower()
        manager = offer.get("sender") or {}
        amounts = (offer.get("receiverSide") or {}).get("amounts") or {}
        price_currency, price_original = _original_price(amounts)
        if price_currency and price_currency != "EUR" and rates is None:
            rates = fetch_exchange_rates()
        conversion_amounts = {
            field: value for field, value in amounts.items()
            if value is not None and str(value) != "0"
        }
        eur_cents = to_eur_cents(conversion_amounts, rates)
        price_eur = Decimal(str(eur_cents)) / Decimal("100") if eur_cents is not None else None
        if (
            not card.get("assetId")
            or not card.get("inSeasonEligible")
            or rarity not in ALLOWED_RARITIES
            or not manager.get("slug")
            or price_eur is None
        ):
            continue
        rows.append({
            "offer_id": offer.get("id"),
            "asset_id": card["assetId"],
            "card_slug": card.get("slug"),
            "picture_url": card.get("pictureUrl"),
            "player": ((card.get("anyPlayer") or {}).get("displayName") or card.get("name") or "Jugador"),
            "rarity": rarity,
            "season_year": card.get("seasonYear"),
            "serial_number": card.get("serialNumber"),
            "grade": card.get("grade"),
            "team": (card.get("anyTeam") or {}).get("name"),
            "manager_slug": manager["slug"],
            "manager": manager.get("nickname") or manager["slug"],
            "price_eur": price_eur,
            "price_currency": price_currency,
            "price_original": price_original,
        })
    return sorted(rows, key=lambda row: (row["rarity"], row["price_eur"], row["serial_number"] or 0))
