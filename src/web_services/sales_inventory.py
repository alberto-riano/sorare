from __future__ import annotations

import re
import time
from typing import Callable

from cartas_para_vender import (
    build_collection_data,
    fetch_cards_and_lineups,
    get_min_price_cached,
)
from sorare_utils import build_headers, fetch_exchange_rates


def _season_label(value) -> str:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return "-"
    return f"{year}-{str(year + 1)[-2:]}"


def collection_display_name(value: str) -> str:
    """Quita del nombre de colección la rareza y temporada redundantes."""
    return re.sub(
        r"\s+(?:Limited|Rare|Super Rare|Unique)\s+\d{4}(?:-\d{2,4})?$",
        "",
        str(value or "-"),
        flags=re.IGNORECASE,
    ).strip()


def collect_sales_inventory(
    rarity: str,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[dict]:
    """Obtiene todas las cartas propias de una rareza, incluidas las bloqueadas."""
    headers = build_headers()
    rates = fetch_exchange_rates()
    all_cards, lineup_slugs = fetch_cards_and_lineups(headers, rarity=rarity)
    collection_rays, collection_player_rays = build_collection_data(all_cards)

    minimum_cache: dict = {}
    rows: list[dict] = []
    total = len(all_cards)
    if progress:
        progress(0, total, "Preparando precios")

    for index, card in enumerate(all_cards, start=1):
        player = card.get("anyPlayer") or {}
        team = card.get("anyTeam") or {}
        player_name = player.get("displayName") or card.get("name") or "Jugador"
        player_slug = player.get("slug") or card.get("slug") or ""
        asset_id = card.get("assetId") or ""
        season_year = card.get("seasonYear")
        card_rarity = card.get("rarityTyped") or rarity
        collection_cards = card.get("cardCollectionCards") or []

        if collection_cards:
            score = collection_cards[0].get("scoreBreakdown") or {}
            card_rays = score.get("total") or 0
            collection_name = (collection_cards[0].get("cardCollection") or {}).get("name") or "-"
        else:
            card_rays = 0
            collection_name = f"{team.get('name') or '-'} {card_rarity} {_season_label(season_year)}"

        player_rays = collection_player_rays.get((collection_name, player_slug), [card_rays])
        remaining = list(player_rays)
        if card_rays in remaining:
            remaining.remove(card_rays)
        best_after = max(remaining) if remaining else 0
        current_best = player_rays[0] if player_rays else card_rays
        rays_after_sale = collection_rays.get(collection_name, 0) - (current_best - best_after)

        min_key = (player_slug, card_rarity)
        min_was_cached = min_key in minimum_cache
        min_classic, min_inseason = get_min_price_cached(
            player_slug, card_rarity, asset_id, headers, rates, minimum_cache
        )
        if not min_was_cached:
            time.sleep(0.15)

        active_offer = card.get("liveSingleSaleOffer") or {}
        offer_amounts = (active_offer.get("receiverSide") or {}).get("amounts") or {}
        tradeable = card.get("tradeableStatus") or "YES"
        in_lineup = card.get("slug") in lineup_slugs
        in_vault = tradeable != "YES"
        active_listing = bool(active_offer.get("id"))
        blocked_reasons = []
        if in_lineup:
            blocked_reasons.append("En lineup")
        if in_vault:
            blocked_reasons.append("En vault")
        if active_listing:
            blocked_reasons.append("Ya está a la venta")

        active_club = player.get("activeClub") or {}
        rows.append({
            "asset_id": asset_id,
            "card_slug": card.get("slug") or "",
            "player": player_name,
            "player_slug": player_slug,
            "player_picture_url": player.get("squaredPictureUrl") or "",
            "team": team.get("name") or "-",
            "team_picture_url": team.get("pictureUrl") or "",
            "rarity": card_rarity,
            "season_year": season_year,
            "season": _season_label(season_year),
            "serial_number": card.get("serialNumber"),
            "position": ", ".join(card.get("anyPositions") or []) or "-",
            "league": (active_club.get("domesticLeague") or {}).get("name") or "-",
            "grade": card.get("grade") or 0,
            "in_season": bool(card.get("inSeasonEligible")),
            "collection_name": collection_name,
            "collection_display_name": collection_display_name(collection_name),
            "collection_rays": collection_rays.get(collection_name, 0),
            "card_rays": card_rays,
            "rays_after_sale": rays_after_sale,
            "min_price_classic": min_classic,
            "min_price_inseason": min_inseason,
            "in_lineup": in_lineup,
            "in_vault": in_vault,
            "tradeable_status": tradeable,
            "active_listing": active_listing,
            "active_offer_id": active_offer.get("id") or "",
            "active_offer_end": active_offer.get("endDate") or "",
            "active_offer_eur": (int(offer_amounts["eurCents"]) / 100) if offer_amounts.get("eurCents") else None,
            "blocked": bool(blocked_reasons),
            "blocked_reason": " · ".join(blocked_reasons),
        })
        if progress:
            progress(index, total, player_name)

    return rows
