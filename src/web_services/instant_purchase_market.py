"""Compras instantáneas Rare In-Season y valoración de sus precios."""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

from sorare_utils import (
    build_headers,
    fetch_exchange_rates,
    graphql_request,
    to_eur_cents,
)
from web_services.opportunity_market import (
    REQUEST_INTERVAL_SECONDS,
    SEASON_YEAR,
    _floor_query,
    _history_query,
    _parse_date,
    learn_rare_ratio,
    robust_sales_reference,
)


HISTORY_DAYS = 30
PRIMARY_OFFER_PAGE_SIZE = 5


def _comparable_kind(price):
    deal = price.get("deal") or {}
    if deal.get("__typename") == "TokenAuction":
        return "auction", "Subasta"
    if deal.get("__typename") == "TokenOffer" and deal.get("type") == "SINGLE_BUY_OFFER":
        return "public", "Oferta pública"
    return None, None


def _recent_comparables(prices, rates, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=HISTORY_DAYS)
    rows = []
    for price in prices or []:
        kind, label = _comparable_kind(price)
        sold_at = _parse_date(price.get("date"))
        card = price.get("card") or {}
        if not kind or not sold_at or sold_at < cutoff:
            continue
        if card.get("seasonYear") != SEASON_YEAR or not card.get("inSeasonEligible"):
            continue
        cents = to_eur_cents(price.get("amounts") or {}, rates)
        if cents and cents > 0:
            rows.append({
                "eur": round(cents / 100, 2),
                "date": price.get("date"),
                "kind": kind,
                "label": label,
            })
    return rows


def build_instant_purchase_rows(listings, histories, market_floors=None, now=None):
    """Valora cada oferta primaria Rare frente a ventas y mercado secundario."""
    now = now or datetime.now(timezone.utc)
    market_floors = market_floors or {}
    player_slugs = {listing.get("player_slug") for listing in listings}

    player_values = []
    summaries = {}
    for player_slug in player_slugs:
        player_row = {"player_slug": player_slug}
        for rarity in ("limited", "rare"):
            summary = robust_sales_reference(histories.get((player_slug, rarity), []), now=now)
            floor = market_floors.get((player_slug, rarity))
            market_value = min(
                [value for value in (floor, summary["value"]) if value is not None],
                default=None,
            )
            player_row[rarity] = {"market_value": market_value}
            summaries[(player_slug, rarity)] = summary
        player_values.append(player_row)

    rare_ratio, ratio_source, ratio_sample = learn_rare_ratio(player_values)
    rows = []
    for listing in listings:
        if listing.get("rarity") != "rare":
            continue
        player_slug = listing.get("player_slug")
        rare_summary = summaries[(player_slug, "rare")]
        limited_summary = summaries[(player_slug, "limited")]
        rare_floor = market_floors.get((player_slug, "rare"))
        limited_floor = market_floors.get((player_slug, "limited"))
        limited_market_value = min(
            [value for value in (limited_floor, limited_summary["value"]) if value is not None],
            default=None,
        )
        limited_parity = limited_market_value * rare_ratio if limited_market_value else None

        references = []
        if rare_summary["value"]:
            references.extend([float(rare_summary["value"])] * 2)
        if rare_floor:
            references.extend([float(rare_floor)] * 2)
        if limited_parity:
            references.append(float(limited_parity))
        estimated_value = statistics.median(references) if references else None
        if estimated_value is not None and rare_floor is not None:
            estimated_value = min(estimated_value, float(rare_floor))

        price = float(listing["price_eur"])
        saving_eur = estimated_value - price if estimated_value is not None else None
        saving_percent = saving_eur / estimated_value * 100 if estimated_value and saving_eur is not None else None
        confidence_score = rare_summary["confidence_score"]
        if rare_floor is not None:
            confidence_score = max(confidence_score, 55)
        if limited_parity is not None:
            confidence_score = max(confidence_score, 48 if ratio_source == "learned" else 32)
        confidence = "high" if confidence_score >= 72 else "medium" if confidence_score >= 43 else "low"

        rows.append({
            **listing,
            "rare_floor": round(float(rare_floor), 2) if rare_floor is not None else None,
            "rare_sales_reference": rare_summary["value"],
            "rare_sales": rare_summary["sales"],
            "limited_floor": round(limited_floor, 2) if limited_floor is not None else None,
            "limited_sales_reference": limited_summary["value"],
            "limited_sales": limited_summary["sales"],
            "limited_parity_reference": round(limited_parity, 2) if limited_parity else None,
            "estimated_value": round(estimated_value, 2) if estimated_value is not None else None,
            "saving_eur": round(saving_eur, 2) if saving_eur is not None else None,
            "saving_percent": round(saving_percent, 1) if saving_percent is not None else None,
            "confidence": confidence,
            "confidence_score": min(100, confidence_score),
            "is_favorable": bool(saving_eur is not None and saving_eur > 0),
        })

    rows.sort(key=lambda row: (row.get("saving_eur") is not None, row.get("saving_eur") or 0), reverse=True)
    return rows, {
        "rare_limited_ratio": rare_ratio,
        "ratio_source": ratio_source,
        "ratio_sample": ratio_sample,
        "active_listings": len(rows),
        "favorable_listings": sum(1 for row in rows if row["is_favorable"]),
    }


def _normalize_primary_offers(offers, team_slugs, rates):
    listings = []
    for offer in offers:
        cents = to_eur_cents(offer.get("price") or {}, rates)
        if not cents or cents <= 0:
            continue
        campaign = offer.get("instantBuyCampaign") or {}
        if campaign.get("soldOut") or campaign.get("remainingSupply") == 0:
            continue
        for card in offer.get("anyCards") or []:
            player = card.get("anyPlayer") or {}
            team = card.get("anyTeam") or {}
            player_slug = player.get("slug")
            rarity = card.get("rarityTyped")
            if team.get("slug") not in team_slugs or rarity != "rare":
                continue
            if card.get("seasonYear") != SEASON_YEAR or not card.get("inSeasonEligible"):
                continue
            listings.append({
                "player": player.get("displayName") or player_slug,
                "player_slug": player_slug,
                "player_picture_url": player.get("squaredPictureUrl"),
                "team": team.get("name") or team.get("slug"),
                "team_slug": team.get("slug"),
                "team_picture_url": team.get("pictureUrl"),
                "position": ((card.get("anyPositions") or ["?"])[0]),
                "offer_id": offer.get("id"),
                "asset_id": card.get("assetId"),
                "card_slug": card.get("slug"),
                "serial": card.get("serialNumber"),
                "rarity": rarity,
                "price_eur": round(cents / 100, 2),
                "start_date": offer.get("startDate"),
                "end_date": offer.get("endDate"),
                                "remaining_at_price": campaign.get("remainingAtCurrentPrice"),
                                "remaining_supply": campaign.get("remainingSupply"),
            })
    return listings


PRIMARY_OFFERS_QUERY = f"""
    query InstantPurchaseListings($after: String) {{
        tokens {{
            livePrimaryOffers(sport: FOOTBALL, first: {PRIMARY_OFFER_PAGE_SIZE}, after: $after) {{
                nodes {{
                    id startDate endDate status
                    price {{ eurCents usdCents gbpCents wei }}
                    instantBuyCampaign {{ remainingAtCurrentPrice remainingSupply soldOut }}
                    anyCards {{
                        slug assetId rarityTyped seasonYear serialNumber inSeasonEligible anyPositions
                        anyPlayer {{ slug displayName squaredPictureUrl }}
                        anyTeam {{ slug name pictureUrl }}
                    }}
                }}
                pageInfo {{ hasNextPage endCursor }}
            }}
        }}
    }}
"""


def _fetch_live_primary_offers(headers, progress=None):
        offers = []
        cursor = None
        page = 0
        while True:
                connection = graphql_request(PRIMARY_OFFERS_QUERY, {"after": cursor}, headers=headers)["tokens"]["livePrimaryOffers"]
                offers.extend(connection.get("nodes") or [])
                page += 1
                if progress:
                        progress(page, 0, f"Ofertas directas de Sorare: {len(offers)} revisadas")
                page_info = connection.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                        break
                cursor = page_info.get("endCursor")
                time.sleep(REQUEST_INTERVAL_SECONDS)
        return offers


def collect_instant_purchase_market(progress=None, team_slugs=None, catalog_callback=None):
    """Descarga listings activos y comparables para los clubes seleccionados."""
    import listar_subastas

    headers = build_headers()
    rates = fetch_exchange_rates()
    teams = listar_subastas.fetch_la_liga_teams(headers, season_year=SEASON_YEAR)
    requested = {str(slug).strip() for slug in (team_slugs or []) if str(slug).strip()}
    unknown = requested.difference(teams)
    if unknown:
        raise ValueError(f"Equipos no válidos: {', '.join(sorted(unknown))}")
    team_items = [item for item in teams.items() if not requested or item[0] in requested]
    catalog = [{"slug": slug, "name": name, "picture_url": ""} for slug, name in teams.items()]
    if catalog_callback:
        catalog_callback(catalog)

    catalog_by_slug = {team["slug"]: team for team in catalog}
    selected_team_slugs = {slug for slug, _ in team_items}
    offers = _fetch_live_primary_offers(headers, progress=progress)
    listings = _normalize_primary_offers(offers, selected_team_slugs, rates)
    for listing in listings:
        catalog_team = catalog_by_slug.get(listing["team_slug"])
        if catalog_team:
            catalog_team["picture_url"] = listing.get("team_picture_url") or catalog_team["picture_url"]
    if catalog_callback:
        catalog_callback(catalog)

    rare_players = sorted({row["player_slug"] for row in listings})
    market_floors = {}
    for index, player_slug in enumerate(rare_players, start=1):
        query, variables = _floor_query({"player_slug": player_slug})
        market = (graphql_request(query, variables, headers=headers).get("player") or {})
        for rarity in ("limited", "rare"):
            nodes = (market.get(rarity) or {}).get("nodes") or []
            card = (nodes[0] if nodes else {}).get("lowestPriceCard") or {}
            offer = card.get("liveSingleSaleOffer") or {}
            cents = to_eur_cents((offer.get("receiverSide") or {}).get("amounts") or {}, rates)
            if cents and cents > 0:
                market_floors[(player_slug, rarity)] = round(cents / 100, 2)
        if progress:
            progress(index, len(rare_players) * 2, f"Suelos de mercado: {index}/{len(rare_players)} jugadores")
        if index < len(rare_players):
            time.sleep(REQUEST_INTERVAL_SECONDS)

    histories = {(slug, rarity): [] for slug in rare_players for rarity in ("limited", "rare")}
    for offset in range(0, len(rare_players), 8):
        chunk = rare_players[offset:offset + 8]
        query, variables = _history_query(chunk)
        prices = graphql_request(query, variables, headers=headers)["tokens"]
        for index, slug in enumerate(chunk):
            for rarity in ("limited", "rare"):
                alias = f"p{index}{'l' if rarity == 'limited' else 'r'}"
                histories[(slug, rarity)] = _recent_comparables(prices.get(alias), rates)
        if progress:
            done = min(offset + len(chunk), len(rare_players))
            progress(len(rare_players) + done, len(rare_players) * 2, f"Histórico: {done}/{len(rare_players)} jugadores")
        time.sleep(REQUEST_INTERVAL_SECONDS)

    rows, metadata = build_instant_purchase_rows(listings, histories, market_floors=market_floors)
    metadata.update({
        "players_analyzed": len(rare_players),
        "team_catalog": catalog,
        "refreshed_team_slugs": [slug for slug, _ in team_items],
    })
    return {"rows": rows, "metadata": metadata}