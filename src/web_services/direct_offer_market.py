from __future__ import annotations

from decimal import Decimal

from sorare_utils import get_live_single_sale_offers


ALLOWED_RARITIES = {"limited", "rare", "super_rare", "unique"}


def _eur_from_amounts(amounts):
    cents = (amounts or {}).get("eurCents")
    if cents is None:
        return None
    return Decimal(str(cents)) / Decimal("100")


def player_in_season_listings(player_slug, *, headers=None):
    rows = []
    for offer in get_live_single_sale_offers(player_slug, headers=headers):
        cards = (offer.get("senderSide") or {}).get("anyCards") or []
        card = cards[0] if cards else {}
        rarity = str(card.get("rarityTyped") or "").lower()
        manager = offer.get("sender") or {}
        price_eur = _eur_from_amounts((offer.get("receiverSide") or {}).get("amounts"))
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
        })
    return sorted(rows, key=lambda row: (row["rarity"], row["price_eur"], row["serial_number"] or 0))