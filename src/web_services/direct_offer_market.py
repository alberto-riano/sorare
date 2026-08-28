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


def check_direct_offer_eur(asset_id, manager_slug, amount_cents, *, headers=None):
        data = graphql_request(
                PREPARE_DIRECT_OFFER_QUERY,
                {
                        "input": {
                                "sendAssetIds": [],
                                "receiveAssetIds": [asset_id],
                                "settlementCurrencies": ["EUR"],
                                "sendAmount": {"amount": str(amount_cents), "currency": "EUR"},
                                "receiverSlug": manager_slug,
                                "clientMutationId": f"eur-check-{asset_id}",
                        }
                },
                headers=headers,
        )
        errors = (data.get("prepareOffer") or {}).get("errors") or []
        return [str(error.get("message") or "").strip() for error in errors if error.get("message")]


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