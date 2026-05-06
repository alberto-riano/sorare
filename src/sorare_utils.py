#!/usr/bin/env python3
"""
Módulo de utilidades para interactuar con la API de Sorare.
Funciones reutilizables para: autenticación, consultas GraphQL,
obtención de precios, conversión de divisas, etc.
"""

import requests
import sys
import os

SORARE_API_URL = "https://api.sorare.com/graphql"
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'config.txt')


# ---------------------------------------------------------------------------
# Configuración y autenticación
# ---------------------------------------------------------------------------

def read_config(config_path=None):
    """Lee las variables del archivo config.txt."""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    config = {}
    with open(config_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    return config


def build_headers(config=None):
    """Construye los headers de autenticación a partir de la config."""
    if config is None:
        config = read_config()
    jwt_token = config.get('JWT_TOKEN')
    jwt_aud = config.get('JWT_AUD', 'myapp')
    if not jwt_token:
        print("Error: JWT_TOKEN no encontrado en config.txt")
        sys.exit(1)
    return {
        'content-type': 'application/json',
        'Authorization': f'Bearer {jwt_token}',
        'JWT-AUD': jwt_aud,
    }


# ---------------------------------------------------------------------------
# Peticiones GraphQL
# ---------------------------------------------------------------------------

def graphql_request(query, variables=None, headers=None):
    """Ejecuta una petición GraphQL contra la API de Sorare."""
    if headers is None:
        headers = build_headers()
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    resp = requests.post(SORARE_API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if 'errors' in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data['data']


def search_players_by_name(query_text, headers=None):
    """Busca jugadores por nombre usando `searchPlayers`.

    Devuelve lista de dicts: {"slug": str, "displayName": str}.
    """
    query = '''
    query SearchPlayers($query: String!) {
      searchPlayers(query: $query) {
        hits {
          __typename
          ... on ComposeTeamBenchCommonPlayer {
            player {
              slug
              displayName
            }
          }
        }
      }
    }
    '''

    data = graphql_request(query, {'query': query_text}, headers=headers)
    hits = (data.get('searchPlayers') or {}).get('hits') or []
    results = []
    for hit in hits:
        player = hit.get('player') if isinstance(hit, dict) else None
        if not player:
            continue
        slug = player.get('slug')
        display_name = player.get('displayName')
        if slug and display_name:
            results.append({'slug': slug, 'displayName': display_name})
    return results


# ---------------------------------------------------------------------------
# Consultas de cartas y ofertas
# ---------------------------------------------------------------------------

def get_card_info(asset_id, headers=None):
    """Obtiene la info de una carta a partir de su assetId."""
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
    data = graphql_request(query, {'assetId': asset_id}, headers=headers)
    return data['tokens']['anyCard']


def get_live_single_sale_offers(player_slug, headers=None):
    """Obtiene todas las ofertas de venta activas para un jugador."""
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
        data = graphql_request(query, variables, headers=headers)
        connection = data['tokens']['liveSingleSaleOffers']
        all_offers.extend(connection['nodes'])

        if not connection['pageInfo']['hasNextPage']:
            break
        cursor = connection['pageInfo']['endCursor']

    return all_offers


def get_recent_prices(player_slug, rarity, season=None, headers=None):
    """Obtiene precios recientes de ventas realizadas para un jugador y rareza."""
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
    data = graphql_request(query, variables, headers=headers)
    return data['tokens']['tokenPrices']


# ---------------------------------------------------------------------------
# Tasas de cambio
# ---------------------------------------------------------------------------

def fetch_exchange_rates():
    """Obtiene tasas de cambio actuales desde APIs gratuitas.
    Devuelve (usd_to_eur, gbp_to_eur, eth_to_eur).
    """
    usd_to_eur = 0.92
    gbp_to_eur = 1.17
    eth_to_eur = 1800.0

    try:
        r = requests.get('https://open.er-api.com/v6/latest/EUR', timeout=5)
        r.raise_for_status()
        rates = r.json()['rates']
        usd_to_eur = 1 / rates['USD']
        gbp_to_eur = 1 / rates['GBP']
    except (requests.RequestException, KeyError, ValueError, TypeError) as e:
        print(f"  Aviso: No se pudieron obtener tasas fiat, usando valores por defecto ({e})")

    try:
        r = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=eur', timeout=5)
        r.raise_for_status()
        eth_to_eur = r.json()['ethereum']['eur']
    except (requests.RequestException, KeyError, ValueError, TypeError) as e:
        print(f"  Aviso: No se pudo obtener precio ETH, usando valor por defecto ({e})")

    return usd_to_eur, gbp_to_eur, eth_to_eur


# ---------------------------------------------------------------------------
# Conversión y formateo de precios
# ---------------------------------------------------------------------------

def to_eur_cents(amounts, rates=None):
    """Convierte cualquier moneda a eurCents aproximados. Devuelve None si no hay datos.
    rates = (usd_to_eur, gbp_to_eur, eth_to_eur)
    """
    if amounts is None:
        return None
    if rates is None:
        rates = (0.92, 1.17, 1800.0)
    usd_to_eur, gbp_to_eur, eth_to_eur = rates

    eur = amounts.get('eurCents')
    if eur is not None:
        return eur
    usd = amounts.get('usdCents')
    if usd is not None:
        return int(usd * usd_to_eur)
    gbp = amounts.get('gbpCents')
    if gbp is not None:
        return int(gbp * gbp_to_eur)
    wei = amounts.get('wei')
    if wei and wei != '0':
        return int(int(wei) / 1e18 * eth_to_eur * 100)
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


def format_eur_equiv(amounts, rates=None):
    """Devuelve el precio convertido a EUR como string."""
    eur = to_eur_cents(amounts, rates)
    if eur is None:
        return "N/A"
    return f"{eur / 100:.2f}€"


def format_eur(eur_cents):
    """Formatea eurCents como string."""
    if eur_cents is None:
        return "N/A"
    return f"{eur_cents / 100:.2f}€"


# ---------------------------------------------------------------------------
# Funciones de alto nivel
# ---------------------------------------------------------------------------

def get_matching_offers(asset_id, headers=None, rates=None):
    """Obtiene las ofertas de venta filtradas por misma rareza que la carta dada.
    Devuelve una lista de dicts ordenada por precio (de menor a mayor).
    """
    card = get_card_info(asset_id, headers=headers)
    player_slug = card['anyPlayer']['slug']
    rarity = card['rarityTyped']

    offers = get_live_single_sale_offers(player_slug, headers=headers)

    matching = []
    for offer in offers:
        cards_in_offer = offer['senderSide']['anyCards']
        for c in cards_in_offer:
            if c['rarityTyped'] == rarity:
                amounts = offer['receiverSide']['amounts']
                sort_price = to_eur_cents(amounts, rates)
                if sort_price is None:
                    sort_price = float('inf')
                matching.append({
                    'name': c['name'],
                  'slug': c.get('slug'),
                    'serial': c['serialNumber'],
                    'season': c['seasonYear'],
                    'grade': c['grade'],
                    'team': c['anyTeam']['name'] if c['anyTeam'] else 'N/A',
                    'amounts': amounts,
                    'sort_price': sort_price,
                    'asset_id': c['assetId'],
                })

    matching.sort(key=lambda x: x['sort_price'])
    return card, matching


def get_min_price_eur(asset_id, headers=None, rates=None):
    """Devuelve el precio mínimo en EUR (como float) de las ofertas de venta
    para cartas de la misma rareza y jugador. Devuelve None si no hay ofertas.
    """
    try:
        _card, matching = get_matching_offers(asset_id, headers=headers, rates=rates)
        if not matching:
            return None
        cheapest_eur_cents = matching[0]['sort_price']
        if cheapest_eur_cents == float('inf'):
            return None
        return cheapest_eur_cents / 100
    except (requests.RequestException, RuntimeError, KeyError, ValueError, TypeError) as e:
        print(f"  Error consultando precio para {asset_id[:20]}...: {e}")
        return None
