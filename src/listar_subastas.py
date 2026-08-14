#!/usr/bin/env python3
"""
Lista las subastas activas de cartas Rare de La Liga.

Muestra: jugador, assetId, equipo y puja actual.

Usa tokens.liveAuctions para obtener TODAS las subastas activas de fútbol
y filtra en cliente por rareza (rare) y equipos de La Liga.
Esto es mucho más rápido (~2min) que paginar allCards por equipo (~5-10min).

Uso:
    python3 ListLaLigaAuctions.py                    # Rare de La Liga (~2min)
    python3 ListLaLigaAuctions.py --team barcelona   # Filtrar por equipo
    python3 ListLaLigaAuctions.py --rarity unique    # Cambiar rareza
"""
import sys
import os
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sorare_utils import graphql_request, build_headers

LA_LIGA_COMPETITION_SLUG = "primera-division-es"
DEFAULT_SEASON_YEAR = 2026
CACHE_PATH = Path(__file__).resolve().parents[1] / "output" / "la_liga_rare_2026_auctions.json"
REQUEST_INTERVAL_SECONDS = 1.1
CARD_SCAN_PAGE_SIZE = 200

LA_LIGA_TEAMS_QUERY = '''
query GetLaLigaTeams($competition: String!, $seasonYear: Int!) {
  football {
    competition(slug: $competition) {
      displayName
      contestants(seasonStartYear: $seasonYear) {
        anyTeam { name slug }
      }
    }
  }
}
'''

BUYING_AUCTIONS_QUERY = '''
query GetBuyingFootballAuctions {
  currentUser {
    nickname
    buyingTokenAuctions(sport: [FOOTBALL]) {
      id
      open
      currentPrice
      endDate
      bestBid {
        amounts { eurCents }
        userBidder { nickname }
      }
      myLastBid { amounts { eurCents } maximumAmounts { eurCents } }
      anyCards {
        assetId
        rarityTyped
        seasonYear
        serialNumber
        anyPlayer { displayName slug squaredPictureUrl }
        anyTeam { name slug pictureUrl }
        anyPositions
      }
    }
  }
}
'''

LIVE_AUCTIONS_QUERY = '''
query GetAllLiveFootballAuctions($after: String, $updatedAfter: ISO8601DateTime) {
  currentUser { nickname }
  tokens {
    liveAuctions(sport: FOOTBALL, first: 50, after: $after, updatedAfter: $updatedAfter) {
      totalCount
      nodes {
        id
        open
        endDate
        bestBid { amounts { eurCents } userBidder { nickname } }
        myLastBid { amounts { eurCents } maximumAmounts { eurCents } }
        anyCards {
          assetId rarityTyped seasonYear serialNumber
          anyPlayer { displayName slug squaredPictureUrl }
          anyTeam { name slug pictureUrl }
          anyPositions
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
'''

LA_LIGA_CARDS_WITH_AUCTIONS_QUERY = '''
query GetLaLigaCardsWithAuctions($after: String, $first: Int!, $teamSlugs: [String!]!, $seasonYears: [Int!]!) {
  currentUser { nickname }
  football {
    allCards(first: $first, after: $after, rarities: [rare], seasonStartYears: $seasonYears, teamSlugs: $teamSlugs) {
      totalCount
      nodes {
        assetId rarityTyped seasonYear serialNumber
        anyPlayer { displayName slug squaredPictureUrl }
        anyTeam { name slug pictureUrl }
        anyPositions
        latestEnglishAuction {
          id open endDate
          bestBid { amounts { eurCents } userBidder { nickname } }
          myLastBid { amounts { eurCents } maximumAmounts { eurCents } }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
'''


def fetch_la_liga_teams(headers, season_year=DEFAULT_SEASON_YEAR):
    """Obtiene de Sorare los clubes que disputan LaLiga en la temporada indicada."""
    data = graphql_request(
        LA_LIGA_TEAMS_QUERY,
        {"competition": LA_LIGA_COMPETITION_SLUG, "seasonYear": season_year},
        headers=headers,
    )
    competition = data["football"]["competition"]
    teams = {}
    for contestant in competition.get("contestants") or []:
        team = contestant.get("anyTeam") or {}
        if team.get("slug"):
            teams[team["slug"]] = team.get("name") or team["slug"]
    if not teams:
        raise RuntimeError(f"Sorare no devolvió equipos de LaLiga para {season_year}-{season_year + 1}")
    return teams


def fetch_all_live_auctions(headers, rarity="rare", team_slugs=None, season_year=DEFAULT_SEASON_YEAR):
    """
    Obtiene las subastas de fútbol disponibles para la cuenta autenticada y
    conserva solo Rare, temporada y clubes solicitados.
    """
    team_slugs = set(team_slugs or [])
    if rarity != "rare":
        raise ValueError("Esta consulta optimizada solo admite cartas Rare")

    data = graphql_request(BUYING_AUCTIONS_QUERY, headers=headers)
    current_user = data.get("currentUser") or {}
    my_nickname = (current_user.get("nickname") or "").strip()
    nodes = current_user.get("buyingTokenAuctions") or []
    results = []

    for auction in nodes:
        if not auction.get("open"):
            continue
        for card in auction.get("anyCards") or []:
            if card.get('rarityTyped') != rarity:
                continue
            if card.get('seasonYear') != season_year:
                continue
            team = card.get('anyTeam')
            if not team or team.get('slug') not in team_slugs:
                continue

            # Extraer precio/puja
            bid_eur = None
            bidder = None
            if auction.get('bestBid') and auction['bestBid'].get('amounts'):
                eur_cents = auction['bestBid']['amounts'].get('eurCents')
                if eur_cents:
                    bid_eur = eur_cents / 100
                bidder = auction['bestBid'].get('userBidder', {}).get('nickname')

            results.append({
                'player': card['anyPlayer']['displayName'],
                'player_slug': card['anyPlayer']['slug'],
                'player_picture_url': card['anyPlayer'].get('squaredPictureUrl'),
                'team': team['name'],
                'team_slug': team['slug'],
                'team_picture_url': team.get('pictureUrl'),
                'serial': card['serialNumber'],
                'season': card['seasonYear'],
                'position': card.get('anyPositions', ['?'])[0],
                'asset_id': card['assetId'],
                'auction_id': auction['id'],
                'bid_eur': bid_eur,
                'bidder': bidder,
                'is_winning': bool(bidder and my_nickname and bidder.casefold() == my_nickname.casefold()),
                'has_bid': bool(auction.get('myLastBid')),
                'my_bid_eur': ((auction.get('myLastBid') or {}).get('maximumAmounts') or {}).get('eurCents'),
                'end_date': auction['endDate'],
            })

    outbid = [item for item in results if item['has_bid'] and not item['is_winning']]
    positions = fetch_bid_positions(headers, outbid, my_nickname)
    for item in results:
        item['is_outbid'] = item['has_bid'] and not item['is_winning']
        item['bid_position'] = 1 if item['is_winning'] else positions.get(item['auction_id'])
        if item['my_bid_eur'] is not None:
            item['my_bid_eur'] /= 100

    print(f"   Revisadas {len(nodes)} subastas disponibles — {len(results)} de LaLiga encontradas")
    return results, 1, len(nodes)


def fetch_bid_positions(headers, auctions, my_nickname):
    """Calcula la posición por máximo de cada manager en una sola consulta."""
    if not auctions or not my_nickname:
        return {}

    variables = {}
    declarations = []
    selections = []
    for index, auction in enumerate(auctions):
        key = f"id{index}"
        variables[key] = auction['auction_id'].replace('EnglishAuction:', '')
        declarations.append(f"${key}: String!")
        selections.append(
            f"""a{index}: auction(id: ${key}) {{
              bestBid {{ userBidder {{ nickname }} }}
              bids(first: 50) {{
                nodes {{ amounts {{ eurCents }} maximumAmounts {{ eurCents }} userBidder {{ nickname }} }}
              }}
            }}"""
        )
    query = "query(" + ", ".join(declarations) + ") { tokens { " + " ".join(selections) + " } }"
    data = graphql_request(query, variables, headers=headers)['tokens']
    positions = {}
    for index, auction in enumerate(auctions):
        detail = data.get(f"a{index}") or {}
        winner = ((detail.get('bestBid') or {}).get('userBidder') or {}).get('nickname', '').strip()
        maxima = {}
        for bid in (detail.get('bids') or {}).get('nodes') or []:
            nickname = ((bid.get('userBidder') or {}).get('nickname') or '').strip()
            cents = (bid.get('maximumAmounts') or {}).get('eurCents')
            if cents is None:
                cents = (bid.get('amounts') or {}).get('eurCents')
            if nickname and cents is not None:
                maxima[nickname] = max(maxima.get(nickname, 0), cents)
        ordered = []
        if winner:
            ordered.append(winner)
        ordered.extend(name for name, _ in sorted(maxima.items(), key=lambda item: item[1], reverse=True) if name.casefold() != winner.casefold())
        for position, nickname in enumerate(ordered, 1):
            if nickname.casefold() == my_nickname.casefold():
                positions[auction['auction_id']] = position
                break
    return positions


def _rows_from_live_auctions(nodes, team_slugs, my_nickname, season_year=DEFAULT_SEASON_YEAR):
    rows = []
    for auction in nodes:
        if not auction.get('open'):
            continue
        best_bid = auction.get('bestBid') or {}
        bidder = (best_bid.get('userBidder') or {}).get('nickname')
        eur_cents = (best_bid.get('amounts') or {}).get('eurCents')
        my_last_bid = auction.get('myLastBid') or {}
        my_max_cents = (my_last_bid.get('maximumAmounts') or {}).get('eurCents')
        for card in auction.get('anyCards') or []:
            team = card.get('anyTeam') or {}
            if card.get('rarityTyped') != 'rare' or card.get('seasonYear') != season_year:
                continue
            if team.get('slug') not in team_slugs:
                continue
            is_winning = bool(bidder and my_nickname and bidder.casefold() == my_nickname.casefold())
            rows.append({
                'player': card['anyPlayer']['displayName'],
                'player_slug': card['anyPlayer']['slug'],
                'player_picture_url': card['anyPlayer'].get('squaredPictureUrl'),
                'team': team['name'],
                'team_slug': team['slug'],
                'team_picture_url': team.get('pictureUrl'),
                'serial': card['serialNumber'],
                'season': card['seasonYear'],
                'position': card.get('anyPositions', ['?'])[0],
                'asset_id': card['assetId'],
                'auction_id': auction['id'],
                'bid_eur': eur_cents / 100 if eur_cents is not None else None,
                'bidder': bidder,
                'is_winning': is_winning,
                'has_bid': bool(my_last_bid),
                'my_bid_eur': my_max_cents / 100 if my_max_cents is not None else None,
                'is_outbid': bool(my_last_bid) and not is_winning,
                'bid_position': 1 if is_winning else None,
                'end_date': auction['endDate'],
            })
    return rows


def fetch_all_team_card_auctions(headers, team_slugs, season_year=DEFAULT_SEASON_YEAR):
    """Escanea todas las cartas de los clubes; es más lento pero no tiene la ventana de 10 días de liveAuctions."""
    cursor = None
    page = 0
    total = 0
    rows = []
    my_nickname = ""
    while True:
        for attempt in range(3):
            try:
                data = graphql_request(
                    LA_LIGA_CARDS_WITH_AUCTIONS_QUERY,
                    {"after": cursor, "first": CARD_SCAN_PAGE_SIZE, "teamSlugs": sorted(team_slugs), "seasonYears": [season_year]},
                    headers=headers,
                )
                break
            except Exception as exc:
                if attempt == 2:
                    raise
                time.sleep(65 if "429" in str(exc) else 5)
        my_nickname = ((data.get("currentUser") or {}).get("nickname") or my_nickname).strip()
        connection = data["football"]["allCards"]
        total = connection["totalCount"]
        page += 1
        for card in connection["nodes"]:
            auction = card.get("latestEnglishAuction")
            if auction and auction.get("open"):
                auction = dict(auction)
                auction["anyCards"] = [{key: value for key, value in card.items() if key != "latestEnglishAuction"}]
                rows.extend(_rows_from_live_auctions([auction], set(team_slugs), my_nickname, season_year))
        pages_total = (total + CARD_SCAN_PAGE_SIZE - 1) // CARD_SCAN_PAGE_SIZE
        scanned = min(page * CARD_SCAN_PAGE_SIZE, total)
        print(f"Página {page}/{pages_total}: {scanned}/{total} cartas, {len(rows)} subastas", flush=True)
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]
        time.sleep(REQUEST_INTERVAL_SECONDS)
    outbid = [row for row in rows if row["is_outbid"]]
    positions = fetch_bid_positions(headers, outbid, my_nickname)
    for row in outbid:
        row["bid_position"] = positions.get(row["auction_id"])
    return rows, page, total, my_nickname


def load_auction_cache():
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def refresh_auction_cache(force_full=False):
    """Reconstruye la caché desde todas las subastas disponibles para la cuenta."""
    headers = build_headers()
    teams = fetch_la_liga_teams(headers, season_year=DEFAULT_SEASON_YEAR)
    previous = load_auction_cache()
    previous_auctions = previous.get('auctions', []) if previous else []
    previous_keys = {(row['auction_id'], row['asset_id']) for row in previous_auctions}
    now = datetime.now(timezone.utc)
    merged, _pages, scanned, my_nickname = fetch_all_team_card_auctions(headers, set(teams), DEFAULT_SEASON_YEAR)
    unique = {(row['auction_id'], row['asset_id']): row for row in merged}
    merged = list(unique.values())
    current_keys = set(unique)
    new_cards_count = len(current_keys - previous_keys)
    last_new_cards_at = (
        now.isoformat() if new_cards_count
        else (previous or {}).get('last_new_cards_at')
    )
    merged.sort(key=lambda row: row['end_date'])

    payload = {
        'updated_at': now.isoformat(),
        'full_refreshed_at': now.isoformat(),
        'my_nickname': my_nickname,
        'scanned_count': scanned,
        'new_cards_count': new_cards_count,
        'last_new_cards_at': last_new_cards_at,
        'auctions': merged,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temporary.replace(CACHE_PATH)
    print(f"Caché actualizada: {len(merged)} subastas Rare de LaLiga 2026-2027 ({new_cards_count} nuevas)")
    return payload


def match_team_slug(partial, team_slugs):
    """Encuentra el slug completo dado un nombre parcial."""
    partial_lower = partial.lower()
    matches = [s for s in team_slugs if partial_lower in s]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        starts = [s for s in matches if s.startswith(partial_lower)]
        if len(starts) == 1:
            return starts[0]
        return matches[0]
    return None


def fetch_la_liga_rare_auctions(team_filters=None, rarity="rare", season_year=DEFAULT_SEASON_YEAR):
    """
    Busca subastas activas de cartas Rare de LaLiga mediante las cartas de los
    participantes oficiales de la temporada.
    """
    if rarity != 'rare' or season_year != DEFAULT_SEASON_YEAR:
        raise ValueError("La caché actual contiene Rare de LaLiga 2026-2027")
    cache = load_auction_cache()
    if not cache:
        raise RuntimeError("La sincronización inicial del mercado está en curso. Vuelve a intentarlo en unos minutos.")
    results = list(cache.get('auctions') or [])

    # Determinar equipos
    if team_filters:
        available_team_slugs = {row['team_slug'] for row in results}
        selected_team_slugs = []
        for t in team_filters:
            slug = match_team_slug(t, available_team_slugs)
            if slug:
                selected_team_slugs.append(slug)
            else:
                print(f"⚠️  Equipo '{t}' no encontrado. Equipos disponibles:")
                for s in available_team_slugs:
                    print(f"     {s}")
                sys.exit(1)
        results = [row for row in results if row['team_slug'] in selected_team_slugs]
    return results


def print_results(auctions):
    """Imprime los resultados."""
    if not auctions:
        print("\n❌ No se encontraron subastas activas con esos filtros.")
        return

    # Ordenar por fecha de fin (más próximas primero)
    auctions.sort(key=lambda x: x['end_date'])

    print(f"\n✅ Encontradas {len(auctions)} subastas:\n")
    print(f"{'Jugador':<25} {'Equipo':<22} {'Pos':<5} {'#':<4} {'Puja':<10} {'Fin':<22} {'Auction ID'}")
    print("=" * 130)

    for a in auctions:
        bid_str = f"{a['bid_eur']:.2f}€" if a['bid_eur'] else "—"
        auction_short = a['auction_id'].replace('EnglishAuction:', '')[:20]
        pos = a['position'][:4]

        print(f"{a['player']:<25} {a['team']:<22} {pos:<5} {a['serial']:<4} {bid_str:<10} {a['end_date']:<22} {auction_short}")

    print(f"\n{'=' * 130}")
    print("\n📋 Para pujar usa:")
    print("   node javascript/pujar_carta.js <auction_id> <puja_en_centimos_EUR>\n")
    print("   Ejemplo: node javascript/pujar_carta.js EnglishAuction:xxxx 800  (= 8.00€)\n")

    # Detalle completo
    print("📋 Detalle:\n")
    for i, a in enumerate(auctions, 1):
        bid_str = f"{a['bid_eur']:.2f}€" if a['bid_eur'] else "Sin pujas"
        print(f"  {i}. {a['player']} ({a['team']}) #{a['serial']} — {a['position']}")
        print(f"     Asset ID:   {a['asset_id']}")
        print(f"     Auction ID: {a['auction_id']}")
        print(f"     Puja actual: {bid_str} (by {a['bidder'] or 'nadie'})")
        print(f"     Finaliza:   {a['end_date']}")
        print()


def main():
    parser = argparse.ArgumentParser(description='Lista subastas activas de La Liga')
    parser.add_argument('--team', action='append', help='Filtrar por equipo (nombre parcial). Se puede repetir.')
    parser.add_argument('--rarity', default='rare', choices=['limited', 'rare', 'super_rare', 'unique'],
                        help='Rareza a buscar (default: rare)')
    parser.add_argument('--season-year', type=int, default=DEFAULT_SEASON_YEAR,
                        help='Año inicial de temporada (default: 2026 para 2026-2027)')
    args = parser.parse_args()

    auctions = fetch_la_liga_rare_auctions(
        team_filters=args.team,
        rarity=args.rarity,
        season_year=args.season_year,
    )
    print_results(auctions)


if __name__ == '__main__':
    main()
