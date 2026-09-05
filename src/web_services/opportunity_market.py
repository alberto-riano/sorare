"""Mercado secundario y cálculo de oportunidades Limited/Rare de LaLiga.

El módulo mantiene dos ideas separadas:

* ``market_value``: valoración observable, que nunca supera el suelo activo.
* ``reference_value``: referencia estadística usada para detectar desajustes entre
  rarezas. Puede superar el suelo y por eso no se presenta como "valor de mercado".

Las subastas no forman parte de este módulo: ni las abiertas ni las ya
terminadas. El histórico comparable se limita a ofertas públicas completadas.
"""

from __future__ import annotations

import math
import statistics
import time
from datetime import datetime, timezone

from sorare_utils import build_headers, fetch_exchange_rates, graphql_request, to_eur_cents


SEASON_YEAR = 2026
RARITIES = ("limited", "rare")
RARITY_API = {"limited": "limited", "rare": "rare"}
MIN_OPPORTUNITY_PERCENT = 12.0
FALLBACK_RARE_RATIO = 4.5
REQUEST_INTERVAL_SECONDS = 1.05

def _parse_date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _median_absolute_deviation(values):
    if not values:
        return 0.0
    median = statistics.median(values)
    return statistics.median(abs(value - median) for value in values)


def robust_sales_reference(sales, now=None):
    """Resume ventas comparables con mediana ponderada y rechazo de atípicos."""
    now = now or datetime.now(timezone.utc)
    valid = [row for row in sales if float(row.get("eur") or 0) > 0]
    if not valid:
        return {"value": None, "confidence": "low", "confidence_score": 0, "sales": [], "dispersion": None}

    prices = [float(row["eur"]) for row in valid]
    center = statistics.median(prices)
    mad = _median_absolute_deviation(prices)
    if len(prices) >= 4:
        scale = max(mad * 1.4826, center * 0.08)
        filtered = [row for row in valid if abs(float(row["eur"]) - center) <= 3.5 * scale]
        if len(filtered) < 2:
            filtered = valid
    else:
        filtered = valid

    weighted = []
    for row in filtered:
        sold_at = _parse_date(row.get("date"))
        age_days = max(0.0, (now - sold_at).total_seconds() / 86400) if sold_at else 45.0
        weight = math.pow(0.5, age_days / 21.0)
        weighted.append((float(row["eur"]), weight))
    weighted.sort(key=lambda pair: pair[0])
    half = sum(weight for _, weight in weighted) / 2
    running = 0.0
    value = weighted[-1][0]
    for price, weight in weighted:
        running += weight
        if running >= half:
            value = price
            break

    kept_prices = [float(row["eur"]) for row in filtered]
    dispersion = (_median_absolute_deviation(kept_prices) / value) if value else 1.0
    newest = max((_parse_date(row.get("date")) for row in filtered), default=None)
    newest_age = (now - newest).days if newest else 999
    count = len(filtered)
    score = min(55, count * 11) + max(0, 25 - min(newest_age, 25)) + max(0, 20 - int(dispersion * 100))
    confidence = "high" if score >= 72 and count >= 4 else "medium" if score >= 43 and count >= 2 else "low"
    return {
        "value": round(value, 2),
        "confidence": confidence,
        "confidence_score": min(100, score),
        "sales": sorted(filtered, key=lambda row: row.get("date") or "", reverse=True),
        "dispersion": round(dispersion, 3),
    }


def _market_value(floor, sales_reference):
    if floor is None:
        return sales_reference
    if sales_reference is None:
        return floor
    return min(float(floor), float(sales_reference))


def estimate_fair_value(
    *,
    sales_reference=None,
    parity_reference=None,
    market_floor_reference=None,
    sales_confidence="low",
    ratio_source="fallback",
):
    """Combina comparables de forma prudente sin usar el precio candidato.

    El histórico reciente pesa más que las referencias indirectas. El suelo se
    considera también un límite de compra, pero debe ser el suelo alternativo:
    incluir la propia ganga ocultaría precisamente el descuento a detectar.
    """
    references = []
    if sales_reference:
        references.append((float(sales_reference), 3 if sales_confidence != "low" else 1))
    if parity_reference:
        references.append((float(parity_reference), 2 if ratio_source == "learned" else 1))
    if market_floor_reference:
        references.append((float(market_floor_reference), 1))
    if not references:
        return None
    total_weight = sum(weight for _, weight in references)
    value = sum(reference * weight for reference, weight in references) / total_weight
    if market_floor_reference:
        value = min(value, float(market_floor_reference))
    return round(value, 2)


def learn_rare_ratio(players):
    ratios = []
    for player in players:
        limited = (player.get("limited") or {}).get("market_value")
        rare = (player.get("rare") or {}).get("market_value")
        if limited and rare and limited > 0:
            ratio = rare / limited
            if 1.5 <= ratio <= 10:
                ratios.append(ratio)
    if len(ratios) < 5:
        return FALLBACK_RARE_RATIO, "fallback", len(ratios)
    center = statistics.median(ratios)
    mad = _median_absolute_deviation(ratios)
    kept = [ratio for ratio in ratios if mad == 0 or abs(ratio - center) <= max(0.75, 3 * mad)]
    return round(statistics.median(kept), 2), "learned", len(kept)


def build_opportunity_rows(players):
    """Calcula valor observado, paridad y descuento para cada jugador."""
    prepared = []
    for player in players:
        row = {key: value for key, value in player.items() if key not in RARITIES}
        for rarity in RARITIES:
            data = dict(player.get(rarity) or {})
            summary = robust_sales_reference(data.get("sales") or [])
            data.update({
                "sales_reference": summary["value"],
                "confidence": summary["confidence"],
                "confidence_score": summary["confidence_score"],
                "sales": summary["sales"],
                "dispersion": summary["dispersion"],
            })
            data["market_value"] = _market_value(data.get("floor"), summary["value"])
            row[rarity] = data
        prepared.append(row)

    ratio, ratio_source, ratio_sample = learn_rare_ratio(prepared)
    for row in prepared:
        candidates = []
        for rarity, other in (("limited", "rare"), ("rare", "limited")):
            data = row[rarity]
            floor = data.get("floor")
            if floor is None:
                continue
            own_reference = data.get("sales_reference")
            other_value = row[other].get("market_value")
            parity = None
            if other_value:
                parity = other_value / ratio if rarity == "limited" else other_value * ratio
            reference = estimate_fair_value(
                sales_reference=own_reference,
                parity_reference=parity,
                sales_confidence=data["confidence"],
                ratio_source=ratio_source,
            )
            discount = max(0.0, (reference - floor) / reference * 100) if reference and reference > floor else 0.0
            signal_score = data["confidence_score"]
            if parity:
                signal_score = max(signal_score, 48 if ratio_source == "learned" else 32)
            signal_confidence = "high" if signal_score >= 72 else "medium" if signal_score >= 43 else "low"
            data.update({
                "parity_reference": round(parity, 2) if parity else None,
                "reference_value": round(reference, 2) if reference else None,
                "discount_percent": round(discount, 1),
                "signal_confidence": signal_confidence,
            })
            if discount >= MIN_OPPORTUNITY_PERCENT and signal_confidence != "low":
                candidates.append((discount, rarity))
        candidates.sort(reverse=True)
        row["recommended_rarity"] = candidates[0][1] if candidates else None
        row["discount_percent"] = round(candidates[0][0], 1) if candidates else 0.0
        row["confidence"] = row[candidates[0][1]]["signal_confidence"] if candidates else "low"
    prepared.sort(key=lambda row: (row.get("discount_percent") or 0, row.get("player") or ""), reverse=True)
    return prepared, {"rare_limited_ratio": ratio, "ratio_source": ratio_source, "ratio_sample": ratio_sample}


def _comparable_kind(price):
    deal = price.get("deal") or {}
    typename = deal.get("__typename")
    if typename == "TokenOffer" and deal.get("type") == "SINGLE_BUY_OFFER":
        return "public", "Oferta pública"
    return None, None


def _history_query(slugs):
    definitions = []
    selections = []
    variables = {}
    for index, slug in enumerate(slugs):
        variable = f"slug{index}"
        definitions.append(f"${variable}: String!")
        variables[variable] = slug
        for rarity in RARITIES:
            alias = f"p{index}{'l' if rarity == 'limited' else 'r'}"
            selections.append(f"""
              {alias}: tokenPrices(playerSlug: ${variable}, rarity: {RARITY_API[rarity]}, season: {SEASON_YEAR}, seasonEligibility: IN_SEASON, first: 20) {{
                amounts {{ eurCents usdCents gbpCents wei }} date
                card {{ seasonYear inSeasonEligible }}
                deal {{
                  __typename
                  ... on TokenOffer {{
                    type
                    senderSide {{ anyCards {{ assetId }} }}
                    receiverSide {{ anyCards {{ assetId }} }}
                  }}
                }}
              }}
            """)
    query = "query OpportunityPrices(" + ",".join(definitions) + ") { tokens {" + "".join(selections) + "} }"
    return query, variables


def _roster_query(team_items):
    definitions = []
    selections = []
    variables = {}
    for index, (team_slug, _) in enumerate(team_items):
        variable = f"team{index}"
        definitions.append(f"${variable}: String!")
        variables[variable] = team_slug
        selections.append(f"""
          t{index}: club(slug: ${variable}) {{
            slug name pictureUrl
            activePlayers(first: 50) {{
              nodes {{ slug displayName squaredPictureUrl anyPositions }}
            }}
          }}
        """)
    query = "query OpportunityRoster(" + ",".join(definitions) + ") { football {" + "".join(selections) + "} }"
    return query, variables


def _floor_query(player):
    # Sorare rechaza varios ``anyPlayer`` en la raíz aunque lleven alias. Por
    # eso cada petición resuelve un jugador y las dos rarezas a la vez.
    query = f"""
      query OpportunityFloor($slug: String!) {{
        player: anyPlayer(slug: $slug) {{
          limited: anyCards(first: 1, rarities: [limited], seasonStartYears: [{SEASON_YEAR}], inSeasonEligible: true) {{
            nodes {{
              lowestPriceCard {{
                assetId slug serialNumber
                liveSingleSaleOffer {{ receiverSide {{ amounts {{ eurCents usdCents gbpCents wei }} }} }}
              }}
            }}
          }}
          rare: anyCards(first: 1, rarities: [rare], seasonStartYears: [{SEASON_YEAR}], inSeasonEligible: true) {{
            nodes {{
              lowestPriceCard {{
                assetId slug serialNumber
                liveSingleSaleOffer {{ receiverSide {{ amounts {{ eurCents wei }} }} }}
              }}
            }}
          }}
        }}
      }}
    """
    return query, {"slug": player["player_slug"]}


def collect_opportunity_market(progress=None, team_slugs=None, catalog_callback=None):
    """Descarga ofertas fijas y ofertas públicas, y devuelve el snapshot de UI."""
    import listar_subastas

    headers = build_headers()
    rates = fetch_exchange_rates()
    teams = listar_subastas.fetch_la_liga_teams(headers, season_year=SEASON_YEAR)
    team_catalog_by_slug = {
        slug: {"slug": slug, "name": name, "picture_url": ""}
        for slug, name in teams.items()
    }
    team_catalog = sorted(team_catalog_by_slug.values(), key=lambda item: item["name"])
    if catalog_callback:
        catalog_callback(team_catalog)
    requested_team_slugs = {str(slug).strip() for slug in (team_slugs or []) if str(slug).strip()}
    unknown_team_slugs = requested_team_slugs.difference(teams)
    if unknown_team_slugs:
        raise ValueError(f"Equipos no válidos: {', '.join(sorted(unknown_team_slugs))}")
    team_items = [item for item in teams.items() if not requested_team_slugs or item[0] in requested_team_slugs]
    refreshed_team_slugs = [slug for slug, _ in team_items]
    roster = {}
    for offset in range(0, len(team_items), 5):
        chunk = team_items[offset:offset + 5]
        query, variables = _roster_query(chunk)
        clubs = graphql_request(query, variables, headers=headers)["football"]
        for index, (requested_slug, fallback_name) in enumerate(chunk):
            club = clubs.get(f"t{index}") or {}
            catalog_team = team_catalog_by_slug.get(requested_slug)
            if catalog_team:
                catalog_team["name"] = club.get("name") or fallback_name
                catalog_team["picture_url"] = club.get("pictureUrl") or ""
            for player in (club.get("activePlayers") or {}).get("nodes") or []:
                slug = player.get("slug")
                if not slug:
                    continue
                roster[slug] = {
                    "player": player.get("displayName") or slug,
                    "player_slug": slug,
                    "player_picture_url": player.get("squaredPictureUrl"),
                    "team": club.get("name") or fallback_name,
                    "team_slug": club.get("slug"),
                    "team_picture_url": club.get("pictureUrl"),
                    "position": ((player.get("anyPositions") or ["?"])[0]),
                }
        if progress:
            progress(min(offset + len(chunk), len(team_items)), len(team_items), "Cargando plantillas de LaLiga")
        time.sleep(REQUEST_INTERVAL_SECONDS)

    team_catalog = sorted(team_catalog_by_slug.values(), key=lambda item: item["name"])
    if catalog_callback:
        catalog_callback(team_catalog)

    players = list(roster.values())
    floors = {}
    floor_total = len(players)
    for index, player in enumerate(players, start=1):
        query, variables = _floor_query(player)
        data = graphql_request(query, variables, headers=headers)
        market = data.get("player") or {}
        for rarity in RARITIES:
            nodes = (market.get(rarity) or {}).get("nodes") or []
            representative = (nodes[0] if nodes else {}).get("lowestPriceCard") or {}
            offer = representative.get("liveSingleSaleOffer") or {}
            cents = to_eur_cents((offer.get("receiverSide") or {}).get("amounts") or {}, rates)
            if cents is None or cents <= 0:
                continue
            floors[(player["player_slug"], rarity)] = {
                **player,
                "floor": round(cents / 100, 2),
                "asset_id": representative.get("assetId"),
                "card_slug": representative.get("slug"),
                "serial": representative.get("serialNumber"),
            }
        if progress:
            progress(len(team_items) + index, len(team_items) + floor_total, f"Suelos fijos: {index}/{floor_total} jugadores")
        if index < floor_total:
            time.sleep(REQUEST_INTERVAL_SECONDS)

    # El histórico también puede aportar una valoración cuando no existe un
    # suelo activo. Además, un precio en una moneda no contemplada no debe
    # impedir que el jugador llegue a esta fase. Por eso se revisa la plantilla
    # completa y no solo los jugadores para los que ya se detectó un suelo.
    player_slugs = sorted(roster)
    histories = {(slug, rarity): [] for slug in player_slugs for rarity in RARITIES}
    history_total = len(player_slugs)
    for offset in range(0, history_total, 8):
        chunk = player_slugs[offset:offset + 8]
        query, variables = _history_query(chunk)
        prices = graphql_request(query, variables, headers=headers)["tokens"]
        for index, slug in enumerate(chunk):
            for rarity in RARITIES:
                alias = f"p{index}{'l' if rarity == 'limited' else 'r'}"
                comparable = []
                for price in prices.get(alias) or []:
                    kind, label = _comparable_kind(price)
                    if not kind:
                        continue
                    card = price.get("card") or {}
                    if card.get("seasonYear") != SEASON_YEAR or not card.get("inSeasonEligible"):
                        continue
                    cents = to_eur_cents(price.get("amounts") or {}, rates)
                    if cents:
                        comparable.append({
                            "eur": round(cents / 100, 2), "date": price.get("date"),
                            "kind": kind, "label": label,
                        })
                histories[(slug, rarity)] = comparable
        done = min(offset + len(chunk), history_total)
        if progress:
            base = len(team_items) + floor_total
            progress(base + done, base + history_total, f"Histórico: {done}/{history_total} jugadores")
        time.sleep(REQUEST_INTERVAL_SECONDS)

    by_player = {
        slug: {
            key: player.get(key) for key in (
                "player", "player_slug", "player_picture_url", "team", "team_slug",
                "team_picture_url", "position",
            )
        } | {
            rarity: {"sales": histories[(slug, rarity)]}
            for rarity in RARITIES
        }
        for slug, player in roster.items()
    }
    for (slug, rarity), listing in floors.items():
        by_player[slug][rarity].update(listing)
    rows, metadata = build_opportunity_rows(list(by_player.values()))
    metadata.update({
        "roster_players": len(roster),
        "players_analyzed": len(rows),
        "active_listings": len(floors),
        "opportunities": sum(1 for row in rows if row.get("recommended_rarity")),
        "team_catalog": team_catalog,
        "refreshed_team_slugs": refreshed_team_slugs,
    })
    return {"rows": rows, "metadata": metadata}
