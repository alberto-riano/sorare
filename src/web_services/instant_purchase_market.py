"""Compras instantáneas Rare In-Season y valoración de sus precios."""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
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
    _history_query,
    _parse_date,
    _roster_query,
    learn_rare_ratio,
    robust_sales_reference,
)


HISTORY_DAYS = 30
PLAYER_BATCH_SIZE = 1


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


def build_instant_purchase_rows(listings, histories, now=None):
    """Valora cada listing Rare frente a ventas, siguiente suelo y Limited."""
    now = now or datetime.now(timezone.utc)
    by_player = defaultdict(lambda: {"rare": [], "limited": []})
    for listing in listings:
        rarity = listing.get("rarity")
        if rarity in {"rare", "limited"} and listing.get("price_eur"):
            by_player[listing.get("player_slug")][rarity].append(listing)

    player_values = []
    summaries = {}
    for player_slug, market in by_player.items():
        player_row = {"player_slug": player_slug}
        for rarity in ("limited", "rare"):
            summary = robust_sales_reference(histories.get((player_slug, rarity), []), now=now)
            floor = min(
                (float(row["price_eur"]) for row in market[rarity]),
                default=None,
            )
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
        player_market = by_player[player_slug]
        rare_summary = summaries[(player_slug, "rare")]
        limited_summary = summaries[(player_slug, "limited")]
        other_rare_prices = [
            float(row["price_eur"])
            for row in player_market["rare"]
            if row.get("offer_id") != listing.get("offer_id")
        ]
        next_rare_floor = min(other_rare_prices, default=None)
        limited_floor = min(
            (float(row["price_eur"]) for row in player_market["limited"]),
            default=None,
        )
        limited_market_value = min(
            [value for value in (limited_floor, limited_summary["value"]) if value is not None],
            default=None,
        )
        limited_parity = limited_market_value * rare_ratio if limited_market_value else None

        references = []
        if rare_summary["value"]:
            references.extend([float(rare_summary["value"])] * 2)
        if next_rare_floor:
            references.extend([next_rare_floor] * 2)
        if limited_parity:
            references.append(float(limited_parity))
        estimated_value = statistics.median(references) if references else None
        if estimated_value is not None and next_rare_floor is not None:
            estimated_value = min(estimated_value, next_rare_floor)

        price = float(listing["price_eur"])
        saving_eur = estimated_value - price if estimated_value is not None else None
        saving_percent = saving_eur / estimated_value * 100 if estimated_value and saving_eur is not None else None
        confidence_score = rare_summary["confidence_score"]
        if next_rare_floor is not None:
            confidence_score = max(confidence_score, 55)
        if limited_parity is not None:
            confidence_score = max(confidence_score, 48 if ratio_source == "learned" else 32)
        confidence = "high" if confidence_score >= 72 else "medium" if confidence_score >= 43 else "low"

        rows.append({
            **listing,
            "next_rare_floor": round(next_rare_floor, 2) if next_rare_floor is not None else None,
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


def _normalize_live_offers(offers, roster, rates):
    listings = []
    for offer in offers:
        cents = to_eur_cents((offer.get("receiverSide") or {}).get("amounts") or {}, rates)
        if not cents or cents <= 0:
            continue
        for card in (offer.get("senderSide") or {}).get("anyCards") or []:
            player = card.get("anyPlayer") or {}
            player_slug = player.get("slug")
            rarity = card.get("rarityTyped")
            if player_slug not in roster or rarity not in {"rare", "limited"}:
                continue
            if card.get("seasonYear") != SEASON_YEAR or not card.get("inSeasonEligible"):
                continue
            identity = roster[player_slug]
            listings.append({
                **identity,
                "offer_id": offer.get("id"),
                "asset_id": card.get("assetId"),
                "card_slug": card.get("slug"),
                "serial": card.get("serialNumber"),
                "rarity": rarity,
                "price_eur": round(cents / 100, 2),
                "start_date": offer.get("startDate"),
                "end_date": offer.get("endDate"),
            })
    return listings


def _live_offers_query(player_cursors):
        definitions = []
        selections = []
        variables = {}
        for index, (player_slug, cursor) in enumerate(player_cursors):
                slug_variable = f"slug{index}"
                cursor_variable = f"after{index}"
                definitions.extend((f"${slug_variable}: String!", f"${cursor_variable}: String"))
                variables[slug_variable] = player_slug
                variables[cursor_variable] = cursor
                selections.append(f"""
                      p{index}: liveSingleSaleOffers(playerSlug: ${slug_variable}, sport: FOOTBALL, first: 10, after: ${cursor_variable}) {{
                        nodes {{
                            id startDate endDate
                            senderSide {{
                                anyCards {{
                                    slug assetId rarityTyped seasonYear serialNumber inSeasonEligible
                                    anyPlayer {{ slug }}
                                }}
                            }}
                            receiverSide {{ amounts {{ eurCents usdCents gbpCents wei }} }}
                        }}
                        pageInfo {{ hasNextPage endCursor }}
                    }}
                """)
        query = "query InstantPurchaseListings(" + ",".join(definitions) + ") { tokens {" + "".join(selections) + "} }"
        return query, variables


def _fetch_roster_live_offers(player_slugs, headers, progress=None, progress_base=0):
        offers = []
        for offset in range(0, len(player_slugs), PLAYER_BATCH_SIZE):
                chunk = player_slugs[offset:offset + PLAYER_BATCH_SIZE]
                pending = [(slug, None) for slug in chunk]
                while pending:
                        query, variables = _live_offers_query(pending)
                        connections = graphql_request(query, variables, headers=headers)["tokens"]
                        next_pending = []
                        for index, (player_slug, _) in enumerate(pending):
                                connection = connections.get(f"p{index}") or {}
                                offers.extend(connection.get("nodes") or [])
                                page_info = connection.get("pageInfo") or {}
                                if page_info.get("hasNextPage"):
                                        next_pending.append((player_slug, page_info.get("endCursor")))
                        pending = next_pending
                        if pending:
                                time.sleep(REQUEST_INTERVAL_SECONDS)
                done = min(offset + len(chunk), len(player_slugs))
                if progress:
                        progress(progress_base + done, progress_base + len(player_slugs), f"Compras activas: {done}/{len(player_slugs)} jugadores")
                if done < len(player_slugs):
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

    roster = {}
    catalog_by_slug = {team["slug"]: team for team in catalog}
    for offset in range(0, len(team_items), 5):
        chunk = team_items[offset:offset + 5]
        query, variables = _roster_query(chunk)
        clubs = graphql_request(query, variables, headers=headers)["football"]
        for index, (requested_slug, fallback_name) in enumerate(chunk):
            club = clubs.get(f"t{index}") or {}
            catalog_team = catalog_by_slug[requested_slug]
            catalog_team["name"] = club.get("name") or fallback_name
            catalog_team["picture_url"] = club.get("pictureUrl") or ""
            for player in (club.get("activePlayers") or {}).get("nodes") or []:
                if not player.get("slug"):
                    continue
                roster[player["slug"]] = {
                    "player": player.get("displayName") or player["slug"],
                    "player_slug": player["slug"],
                    "player_picture_url": player.get("squaredPictureUrl"),
                    "team": catalog_team["name"],
                    "team_slug": requested_slug,
                    "team_picture_url": catalog_team["picture_url"],
                    "position": ((player.get("anyPositions") or ["?"])[0]),
                }
        if progress:
            progress(min(offset + len(chunk), len(team_items)), len(team_items), "Cargando plantillas de LaLiga")
        time.sleep(REQUEST_INTERVAL_SECONDS)
    if catalog_callback:
        catalog_callback(catalog)

    player_slugs = sorted(roster)
    offers = _fetch_roster_live_offers(
        player_slugs,
        headers,
        progress=progress,
        progress_base=len(team_items),
    )
    listings = _normalize_live_offers(offers, roster, rates)
    rare_players = sorted({row["player_slug"] for row in listings if row["rarity"] == "rare"})
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
            base = len(team_items) + len(player_slugs)
            progress(base + done, base + len(rare_players), f"Valorando cartas: {done}/{len(rare_players)} jugadores")
        time.sleep(REQUEST_INTERVAL_SECONDS)

    rows, metadata = build_instant_purchase_rows(listings, histories)
    metadata.update({
        "players_analyzed": len(rare_players),
        "team_catalog": catalog,
        "refreshed_team_slugs": [slug for slug, _ in team_items],
    })
    return {"rows": rows, "metadata": metadata}