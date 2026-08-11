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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sorare_utils import graphql_request, build_headers

LA_LIGA_COMPETITION_SLUG = "primera-division-es"
DEFAULT_SEASON_YEAR = 2026

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

LA_LIGA_CARDS_WITH_AUCTIONS_QUERY = '''
query GetLaLigaCardsWithAuctions($after: String, $teamSlugs: [String!]!, $seasonYears: [Int!]!) {
  football {
    allCards(
      first: 50
      after: $after
      rarities: [rare]
      seasonStartYears: $seasonYears
      teamSlugs: $teamSlugs
    ) {
      totalCount
      nodes {
        assetId
        rarityTyped
        seasonYear
        serialNumber
        anyPlayer { displayName slug }
        anyTeam { name slug }
        anyPositions
        latestEnglishAuction {
          id
          open
          currentPrice
          endDate
          bestBid {
            amounts { eurCents }
            userBidder { nickname }
          }
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
    Pagina solo las cartas Rare de los clubes y temporada solicitados, y conserva
    las que tienen una subasta abierta. Evita recorrer el mercado global.
    """
    team_slugs = set(team_slugs or [])
    if rarity != "rare":
        raise ValueError("Esta consulta optimizada solo admite cartas Rare")

    results = []
    cursor = None
    page = 0
    total = None

    while True:
        try:
            data = graphql_request(
                LA_LIGA_CARDS_WITH_AUCTIONS_QUERY,
                {"after": cursor, "teamSlugs": sorted(team_slugs), "seasonYears": [season_year]},
                headers=headers,
            )
        except Exception as e:
            print(f"  ⚠️  Error en página {page + 1}: {e}", file=sys.stderr)
            break

        cards_connection = data['football']['allCards']
        nodes = cards_connection['nodes']
        page += 1

        if total is None:
            total = cards_connection['totalCount']

        for card in nodes:
            auction = card.get('latestEnglishAuction')
            if not auction or not auction.get('open'):
                continue

            # Filtrar por rareza
            if card.get('rarityTyped') != rarity:
                continue

            # 2026 representa la temporada europea 2026-2027 en Sorare.
            if card.get('seasonYear') != season_year:
                continue

            # Filtrar por equipo
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
                'team': team['name'],
                'team_slug': team['slug'],
                'serial': card['serialNumber'],
                'season': card['seasonYear'],
                'position': card.get('anyPositions', ['?'])[0],
                'asset_id': card['assetId'],
                'auction_id': auction['id'],
                'bid_eur': bid_eur,
                'bidder': bidder,
                'end_date': auction['endDate'],
            })

        # Progreso
        pages_total = (total + 49) // 50 if total else '?'
        print(f"\r   Página {page}/{pages_total} — {len(results)} subastas La Liga encontradas", end="", flush=True)

        if not cards_connection['pageInfo']['hasNextPage']:
            break
        cursor = cards_connection['pageInfo']['endCursor']

    print()  # newline after progress
    return results, page, total


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
    headers = build_headers()
    la_liga_teams = fetch_la_liga_teams(headers, season_year=season_year)

    # Determinar equipos
    if team_filters:
        team_slugs = []
        for t in team_filters:
            slug = match_team_slug(t, la_liga_teams)
            if slug:
                team_slugs.append(slug)
            else:
                print(f"⚠️  Equipo '{t}' no encontrado. Equipos disponibles:")
                for s in la_liga_teams:
                    print(f"     {s}")
                sys.exit(1)
    else:
        team_slugs = list(la_liga_teams)

    team_filter_str = f" (equipos: {', '.join(team_filters)})" if team_filters else ""
    print(f"🔍 Buscando subastas {rarity} de LaLiga {season_year}-{season_year + 1}{team_filter_str}...")
    print("   Consultando cartas de los clubes y conservando las subastas abiertas...\n")

    start = time.time()
    results, pages, total = fetch_all_live_auctions(
        headers,
        rarity=rarity,
        team_slugs=team_slugs,
        season_year=season_year,
    )
    elapsed = time.time() - start

    print(f"   Escaneadas {total} subastas en {pages} páginas ({elapsed:.1f}s)")
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
