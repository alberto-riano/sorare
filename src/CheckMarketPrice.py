#!/usr/bin/env python3
"""
Script para consultar el precio de mercado de cartas similares en Sorare.
Dado un assetId, busca todas las cartas del mismo jugador y rareza que están a la venta.
"""

import requests
import sys
import os

# ============================================================
# CONFIGURACIÓN: Cambia el assetId de la carta que quieras consultar
# ============================================================
ASSET_ID = "0x04001efe727e6032cf81edae019cc577d9f740563d8b0b3acc105ab273c19756"
# Sergi Guardiola • Rare #5 (2022)
# ============================================================

SORARE_API_URL = "https://api.sorare.com/graphql"

# Ruta al config.txt: siempre relativa al directorio de este script (../config.txt)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.txt')


def read_config(config_path=CONFIG_PATH):
    config = {}
    with open(config_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    return config


config = read_config()
JWT_TOKEN = config.get('JWT_TOKEN')
JWT_AUD = config.get('JWT_AUD', 'myapp')

if not JWT_TOKEN:
    print("Error: JWT_TOKEN no encontrado en config.txt")
    sys.exit(1)

HEADERS = {
    'content-type': 'application/json',
    'Authorization': f'Bearer {JWT_TOKEN}',
    'JWT-AUD': JWT_AUD,
}


def graphql_request(query, variables=None):
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    resp = requests.post(SORARE_API_URL, json=payload, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if 'errors' in data:
        print(f"GraphQL errors: {data['errors']}")
        sys.exit(1)
    return data['data']


def get_card_info(asset_id):
    """Obtiene la info de la carta a partir de su assetId."""
    query = '''
    query GetCardByAssetId($assetId: String!) {
      tokens {
        anyCard(assetId: $assetId) {
          name
          slug
          assetId
          rarityTyped
          seasonYear
          serialNumber
          anyPlayer {
            slug
            displayName
          }
          anyTeam {
            name
          }
          liveSingleSaleOffer {
            senderSide {
              amounts {
                eurCents
                wei
              }
            }
          }
          publicMinPrices {
            eurCents
            wei
          }
          privateMinPrices {
            eurCents
            wei
          }
        }
      }
    }
    '''
    data = graphql_request(query, {'assetId': asset_id})
    return data['tokens']['anyCard']


def get_live_single_sale_offers(player_slug):
    """Obtiene todas las ofertas de venta en el mercado para un jugador."""
    all_offers = []
    cursor = None

    query = '''
    query GetLiveSaleOffers($playerSlug: String, $first: Int!, $after: String) {
      tokens {
        liveSingleSaleOffers(playerSlug: $playerSlug, sport: FOOTBALL, first: $first, after: $after) {
          nodes {
            id
            startDate
            endDate
            senderSide {
              amounts {
                eurCents
                usdCents
                gbpCents
                wei
              }
              anyCards {
                name
                slug
                assetId
                rarityTyped
                seasonYear
                serialNumber
                grade
                anyPlayer {
                  slug
                  displayName
                }
                anyTeam {
                  name
                }
              }
            }
            receiverSide {
              amounts {
                eurCents
                usdCents
                gbpCents
                wei
              }
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
          totalCount
        }
      }
    }
    '''

    while True:
        variables = {
            'playerSlug': player_slug,
            'first': 50,
            'after': cursor,
        }
        data = graphql_request(query, variables)
        connection = data['tokens']['liveSingleSaleOffers']
        all_offers.extend(connection['nodes'])

        if not connection['pageInfo']['hasNextPage']:
            break
        cursor = connection['pageInfo']['endCursor']

    return all_offers


def get_recent_prices(player_slug, rarity, season=None):
    """Obtiene precios recientes de ventas realizadas para ese jugador y rareza."""
    query = '''
    query GetTokenPrices($playerSlug: String!, $rarity: Rarity!, $season: Int) {
      tokens {
        tokenPrices(playerSlug: $playerSlug, rarity: $rarity, season: $season, first: 10) {
          amounts {
            eurCents
            wei
          }
          date
          card {
            name
            serialNumber
            seasonYear
            grade
          }
        }
      }
    }
    '''
    variables = {
        'playerSlug': player_slug,
        'rarity': rarity,
    }
    if season is not None:
        variables['season'] = season
    data = graphql_request(query, variables)
    return data['tokens']['tokenPrices']


# Tasas de conversión en vivo a EUR
def fetch_exchange_rates():
    """Obtiene tasas de cambio actuales desde APIs gratuitas."""
    usd_to_eur = 0.92
    gbp_to_eur = 1.17
    eth_to_eur = 1800.0

    # Fiat: open.er-api.com (sin API key)
    try:
        r = requests.get('https://open.er-api.com/v6/latest/EUR', timeout=5)
        r.raise_for_status()
        rates = r.json()['rates']
        usd_to_eur = 1 / rates['USD']
        gbp_to_eur = 1 / rates['GBP']
    except Exception as e:
        print(f"  Aviso: No se pudieron obtener tasas fiat, usando valores por defecto ({e})")

    # ETH: CoinGecko (sin API key)
    try:
        r = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=eur', timeout=5)
        r.raise_for_status()
        eth_to_eur = r.json()['ethereum']['eur']
    except Exception as e:
        print(f"  Aviso: No se pudo obtener precio ETH, usando valor por defecto ({e})")

    return usd_to_eur, gbp_to_eur, eth_to_eur


print("Obteniendo tasas de cambio actuales...")
USD_TO_EUR, GBP_TO_EUR, ETH_TO_EUR = fetch_exchange_rates()
print(f"  1 USD = {USD_TO_EUR:.4f} EUR | 1 GBP = {GBP_TO_EUR:.4f} EUR | 1 ETH = {ETH_TO_EUR:.2f} EUR")


def to_eur_cents(amounts):
    """Convierte cualquier moneda a eurCents aproximados. Devuelve None si no hay datos."""
    if amounts is None:
        return None
    eur = amounts.get('eurCents')
    if eur is not None:
        return eur
    usd = amounts.get('usdCents')
    if usd is not None:
        return int(usd * USD_TO_EUR)
    gbp = amounts.get('gbpCents')
    if gbp is not None:
        return int(gbp * GBP_TO_EUR)
    wei = amounts.get('wei')
    if wei and wei != '0':
        return int(int(wei) / 1e18 * ETH_TO_EUR * 100)
    return None


def format_price(amounts):
    """Formatea el precio en su moneda original."""
    if amounts is None:
        return "N/A"
    eur_cents = amounts.get('eurCents')
    if eur_cents is not None:
        return f"{eur_cents / 100:.2f}€"
    usd_cents = amounts.get('usdCents')
    if usd_cents is not None:
        return f"${usd_cents / 100:.2f}"
    gbp_cents = amounts.get('gbpCents')
    if gbp_cents is not None:
        return f"£{gbp_cents / 100:.2f}"
    wei = amounts.get('wei')
    if wei and wei != '0':
        eth = int(wei) / 1e18
        return f"{eth:.6f} ETH"
    return "N/A"


def format_eur_equiv(amounts):
    """Devuelve el precio convertido a EUR."""
    eur = to_eur_cents(amounts)
    if eur is None:
        return "N/A"
    return f"{eur / 100:.2f}€"


def format_eur(eur_cents):
    if eur_cents is None:
        return "N/A"
    return f"{eur_cents / 100:.2f}€"


def main():
    print(f"{'=' * 70}")
    print(f"  SORARE - Consulta de precios de mercado de cartas similares")
    print(f"{'=' * 70}")
    print(f"\nAsset ID: {ASSET_ID}\n")

    # Paso 1: Obtener info de la carta
    print("Obteniendo información de la carta...")
    card = get_card_info(ASSET_ID)

    player_name = card['anyPlayer']['displayName']
    player_slug = card['anyPlayer']['slug']
    rarity = card['rarityTyped']
    season = card['seasonYear']
    team = card['anyTeam']['name'] if card['anyTeam'] else 'Sin equipo'
    serial = card['serialNumber']

    print(f"\n  Jugador:  {player_name}")
    print(f"  Equipo:   {team}")
    print(f"  Rareza:   {rarity}")
    print(f"  Temporada: {season}")
    print(f"  Serial:   #{serial}")
    print(f"  Slug:     {card['slug']}")

    if card.get('publicMinPrices') and card['publicMinPrices'].get('eurCents'):
        print(f"  Precio mín. público: {format_eur(card['publicMinPrices']['eurCents'])}")
    if card.get('privateMinPrices') and card['privateMinPrices'].get('eurCents'):
        print(f"  Precio mín. privado: {format_eur(card['privateMinPrices']['eurCents'])}")

    if card.get('liveSingleSaleOffer'):
        offer = card['liveSingleSaleOffer']
        price = offer['senderSide']['amounts'].get('eurCents')
        print(f"  Tu carta está a la venta por: {format_eur(price)}")

    # Paso 2: Buscar ofertas de venta en el mercado
    print(f"\n{'=' * 70}")
    print(f"  Buscando cartas de {player_name} ({rarity}) a la venta...")
    print(f"{'=' * 70}\n")

    offers = get_live_single_sale_offers(player_slug)

    # Filtrar por misma rareza
    matching_offers = []
    for offer in offers:
        cards_in_offer = offer['senderSide']['anyCards']
        for c in cards_in_offer:
            if c['rarityTyped'] == rarity:
                amounts = offer['receiverSide']['amounts']
                sort_price = to_eur_cents(amounts)
                if sort_price is None:
                    sort_price = float('inf')
                matching_offers.append({
                    'name': c['name'],
                    'serial': c['serialNumber'],
                    'season': c['seasonYear'],
                    'grade': c['grade'],
                    'team': c['anyTeam']['name'] if c['anyTeam'] else 'N/A',
                    'amounts': amounts,
                    'sort_price': sort_price,
                    'asset_id': c['assetId'],
                })

    # Ordenar por precio
    matching_offers.sort(key=lambda x: x['sort_price'])

    if matching_offers:
        print(f"Se encontraron {len(matching_offers)} cartas {rarity} de {player_name} a la venta:\n")
        print(f"  {'#':<4} {'Serial':<10} {'Temp.':<8} {'Nivel':<8} {'Precio':<16} {'(~EUR)':<12} {'Asset ID'}")
        print(f"  {'-'*4} {'-'*10} {'-'*8} {'-'*8} {'-'*16} {'-'*12} {'-'*34}")

        for i, o in enumerate(matching_offers, 1):
            price_str = format_price(o['amounts'])
            eur_str = format_eur_equiv(o['amounts'])
            print(f"  {i:<4} #{o['serial']:<9} {o['season']:<8} {o['grade']:<8} {price_str:<16} {eur_str:<12} {o['asset_id'][:34]}")

        cheapest = matching_offers[0]
        print(f"\n  PRECIO MÍNIMO: {format_price(cheapest['amounts'])} (~{format_eur_equiv(cheapest['amounts'])}) (Serial #{cheapest['serial']}, Temporada {cheapest['season']})")
    else:
        print(f"No se encontraron cartas {rarity} de {player_name} a la venta en el mercado.")

    # Paso 3: Últimas ventas realizadas
    print(f"\n{'=' * 70}")
    print(f"  Últimas ventas realizadas de {player_name} ({rarity})")
    print(f"{'=' * 70}\n")

    recent_prices = get_recent_prices(player_slug, rarity, season)

    if recent_prices:
        print(f"  {'#':<4} {'Fecha':<22} {'Precio':<12} {'Serial':<10} {'Temporada':<12} {'Nivel'}")
        print(f"  {'-'*4} {'-'*22} {'-'*12} {'-'*10} {'-'*12} {'-'*8}")

        for i, p in enumerate(recent_prices, 1):
            price_str = format_eur(p['amounts'].get('eurCents'))
            date_str = p['date'][:19] if p['date'] else 'N/A'
            card_info = p.get('card') or {}
            serial = card_info.get('serialNumber', 'N/A')
            s_year = card_info.get('seasonYear', 'N/A')
            grade = card_info.get('grade', 'N/A')
            print(f"  {i:<4} {date_str:<22} {price_str:<12} #{str(serial):<9} {str(s_year):<12} {grade}")
    else:
        print("  No se encontraron ventas recientes.")

    # También obtener precios de cualquier temporada
    if season:
        print(f"\n  --- Ventas de cualquier temporada ---\n")
        all_prices = get_recent_prices(player_slug, rarity)
        if all_prices:
            print(f"  {'#':<4} {'Fecha':<22} {'Precio':<12} {'Serial':<10} {'Temporada':<12} {'Nivel'}")
            print(f"  {'-'*4} {'-'*22} {'-'*12} {'-'*10} {'-'*12} {'-'*8}")
            for i, p in enumerate(all_prices, 1):
                price_str = format_eur(p['amounts'].get('eurCents'))
                date_str = p['date'][:19] if p['date'] else 'N/A'
                card_info = p.get('card') or {}
                serial = card_info.get('serialNumber', 'N/A')
                s_year = card_info.get('seasonYear', 'N/A')
                grade = card_info.get('grade', 'N/A')
                print(f"  {i:<4} {date_str:<22} {price_str:<12} #{str(serial):<9} {str(s_year):<12} {grade}")
        else:
            print("  No se encontraron ventas recientes de ninguna temporada.")

    print(f"\n{'=' * 70}")
    print(f"  Consulta completada")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    main()
