#!/usr/bin/env python3
"""
Genera un Excel con todas las cartas RARE que NO están en lineup,
incluyendo precio medio de últimas ventas y precio mínimo actual en mercado.
"""
import os
import sys
import time
import json
from collections import defaultdict
import requests
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, numbers
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sorare_utils import (
    graphql_request, build_headers, fetch_exchange_rates,
    get_min_price_eur, get_recent_prices, get_card_info,
)

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', 'output', 'cartas_para_vender.xlsx')
DELAY = 0.5  # segundos entre llamadas a la API
MAX_CARTAS = 20  # Cuántas cartas consultar (pon un número grande para todas)


def fetch_rare_cards_and_lineups(headers):
    """Descarga todas las cartas rare y la lista de slugs en lineup."""
    all_cards = []
    lineup_slugs = set()
    cursor = None
    page = 0

    while True:
        page += 1
        after_clause = f', after: "{cursor}"' if cursor else ''
        query = f"""
        query GetCardsPage {{
          currentUser {{
            cards(rarities: [rare], first: 100{after_clause}) {{
              nodes {{
                assetId
                name
                slug
                rarityTyped
                seasonYear
                serialNumber
                anyPlayer {{
                  slug
                  displayName
                }}
                anyTeam {{
                  name
                }}
                anyPositions
                inSeasonEligible
                cardCollectionCards {{
                  scoreBreakdown {{
                    total
                    owner
                    holding
                    firstOwner
                    specialEdition
                    firstSerialNumber
                    shirtMatchingSerialNumber
                  }}
                  cardCollection {{ name }}
                }}
              }}
              pageInfo {{
                hasNextPage
                endCursor
              }}
            }}
            blockchainCardsInLineups
          }}
        }}
        """
        resp = requests.post(
            "https://api.sorare.com/graphql",
            headers=headers,
            json={"query": query},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL error: {json.dumps(data['errors'])}")

        user = data["data"]["currentUser"]
        cards_data = user["cards"]
        all_cards.extend(cards_data["nodes"])
        lineup_slugs = set(user.get("blockchainCardsInLineups", []))

        if not cards_data["pageInfo"]["hasNextPage"]:
            break
        cursor = cards_data["pageInfo"]["endCursor"]
        print(f"\r  Cargando cartas... página {page}", end='', flush=True)

    print(f"\r  ✅ {len(all_cards)} cartas rare cargadas" + " " * 20)
    return all_cards, lineup_slugs


def build_collection_rayos(all_cards):
    """Agrupa todas las cartas por nombre de colección y suma rayos totales."""
    col_rayos = defaultdict(int)
    for card in all_cards:
        for ccc in card.get('cardCollectionCards', []):
            col_name = (ccc.get('cardCollection') or {}).get('name', '?')
            sb = ccc.get('scoreBreakdown') or {}
            col_rayos[col_name] += sb.get('total', 0)
    return col_rayos


def get_avg_recent_price(player_slug, rarity, season, headers, rates, cache):
    """Devuelve el precio medio de las últimas ventas en EUR, o None.
    Usa cache por (player_slug, rarity, season) para evitar duplicados."""
    cache_key = (player_slug, rarity, season)
    if cache_key in cache:
        return cache[cache_key]

    try:
        prices = get_recent_prices(player_slug, rarity, season=season, headers=headers)
        if not prices:
            result = (None, 0)
        else:
            eur_values = []
            for p in prices:
                eur_cents = p.get('amounts', {}).get('eurCents')
                if eur_cents:
                    eur_values.append(int(eur_cents) / 100)
            if not eur_values:
                result = (None, 0)
            else:
                result = (round(sum(eur_values) / len(eur_values), 2), len(eur_values))
    except Exception as e:
        print(f"    ⚠️  Error precios recientes: {e}")
        result = (None, 0)

    cache[cache_key] = result
    return result


def get_min_price_cached(player_slug, rarity, asset_id, headers, rates, cache):
    """Precio mínimo en mercado, cacheado por (player_slug, rarity)."""
    cache_key = (player_slug, rarity)
    if cache_key in cache:
        return cache[cache_key]

    min_price = get_min_price_eur(asset_id, headers=headers, rates=rates)
    cache[cache_key] = min_price
    return min_price


def write_excel(cards_data):
    """Genera el Excel con los datos de las cartas."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cartas para vender"

    # Cabeceras
    # Col: A=Jugador, B=Equipo, C=Temporada, D=Posición,
    #      E=Colección, F=Rayos colección, G=Rayos carta,
    #      H=Precio Medio Ventas, I=Nº Ventas, J=Precio Mín Mercado,
    #      K=Vender, L=Precio venta, M=assetId
    headers_row = ['Jugador', 'Equipo', 'Temporada', 'Posición',
                   'Colección', 'Rayos colección', 'Rayos carta',
                   'Precio Medio Ventas (€)', 'Nº Ventas', 'Precio Mín Mercado (€)',
                   'Vender', 'Precio venta (€)', 'assetId']
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")

    for col, header in enumerate(headers_row, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')

    # Datos
    for i, card in enumerate(cards_data, 2):
        ws.cell(row=i, column=1, value=card['name'])
        ws.cell(row=i, column=2, value=card['team'])
        ws.cell(row=i, column=3, value=card['season'])
        ws.cell(row=i, column=4, value=card['position'])
        ws.cell(row=i, column=5, value=card['collection_name'])
        ws.cell(row=i, column=6, value=card['collection_rayos'])
        ws.cell(row=i, column=7, value=card['card_rayos'])

        avg_cell = ws.cell(row=i, column=8)
        if card['avg_price'] is not None:
            avg_cell.value = card['avg_price']
            avg_cell.number_format = '#,##0.00 €'
        else:
            avg_cell.value = "Sin datos"

        ws.cell(row=i, column=9, value=card['num_sales'])

        min_cell = ws.cell(row=i, column=10)
        if card['min_price'] is not None:
            min_cell.value = card['min_price']
            min_cell.number_format = '#,##0.00 €'
        else:
            min_cell.value = "Sin ofertas"

        # Columna "Vender" vacía (el usuario elige Sí/No)
        ws.cell(row=i, column=11, value='')
        # Columna "Precio venta" vacía
        ws.cell(row=i, column=12, value='')

        ws.cell(row=i, column=13, value=card['asset_id'])

    # Dropdown Sí/No en columna "Vender" (col K)
    if len(cards_data) > 0:
        dv = DataValidation(type='list', formula1='"Sí,No"', allow_blank=True)
        dv.error = 'Selecciona Sí o No'
        dv.errorTitle = 'Valor inválido'
        ws.add_data_validation(dv)
        dv.add(f'K2:K{len(cards_data) + 1}')

    # Ajustar anchos
    widths = [25, 22, 12, 14, 30, 16, 12, 22, 12, 22, 10, 16, 20]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = w

    # Autofiltro
    ws.auto_filter.ref = ws.dimensions

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    wb.save(OUTPUT_PATH)
    print(f"\n💾 Guardado en {os.path.basename(OUTPUT_PATH)}")


def main():
    print("🔄 Conectando con Sorare...")
    headers = build_headers()
    rates = fetch_exchange_rates()

    print("\n📥 Descargando cartas y alineaciones...")
    all_cards, lineup_slugs = fetch_rare_cards_and_lineups(headers)

    # Calcular rayos totales por colección
    collection_rayos = build_collection_rayos(all_cards)

    # Filtrar cartas NO en lineup
    available = [c for c in all_cards if c.get('slug') not in lineup_slugs]
    in_lineup = len(all_cards) - len(available)
    print(f"   {in_lineup} en lineup, {len(available)} disponibles para vender")

    if not available:
        print("✅ No hay cartas disponibles fuera de lineup")
        return

    # Ordenar por nombre
    available.sort(key=lambda c: (c.get('anyPlayer', {}).get('displayName', '') or c.get('name', '')))

    # Limitar cantidad
    if MAX_CARTAS < len(available):
        print(f"   Limitado a {MAX_CARTAS} cartas (de {len(available)})")
        available = available[:MAX_CARTAS]

    # Consultar precios (con cache para no repetir llamadas)
    cards_data = []
    total = len(available)
    price_cache = {}  # (player_slug, rarity, season) → (avg_price, num_sales)
    min_price_cache = {}  # (player_slug, rarity) → min_price
    print(f"\n📊 Consultando precios de {total} cartas...")

    for i, card in enumerate(available):
        player = card.get('anyPlayer') or {}
        player_name = player.get('displayName', card.get('name', '?'))
        player_slug = player.get('slug', '')
        team = (card.get('anyTeam') or {}).get('name', '?')
        season = card.get('seasonYear')
        rarity = card.get('rarityTyped', 'rare')
        asset_id = card.get('assetId', '')
        positions = card.get('anyPositions', [])
        pos_str = ', '.join(positions) if positions else '?'

        # Rayos de la carta y colección desde scoreBreakdown
        ccc_list = card.get('cardCollectionCards', [])
        if ccc_list:
            sb = ccc_list[0].get('scoreBreakdown') or {}
            card_rayos = sb.get('total', 0)
            collection_name = (ccc_list[0].get('cardCollection') or {}).get('name', '?')
        else:
            card_rayos = 0
            season_str = f"{season}-{str(season+1)[-2:]}" if season else '?'
            collection_name = f"{team} Rare {season_str}"
        col_total_rayos = collection_rayos.get(collection_name, 0)

        pct = (i + 1) / total * 100
        print(f"\r  [{i+1}/{total}] {pct:.0f}% - {player_name:<25}", end='', flush=True)

        # Precio medio últimas ventas (cacheado por jugador+rareza+temporada)
        avg_price, num_sales = get_avg_recent_price(
            player_slug, rarity, season, headers, rates, price_cache)
        time.sleep(DELAY)

        # Precio mínimo en mercado (cacheado por jugador+rareza)
        min_price = get_min_price_cached(
            player_slug, rarity, asset_id, headers, rates, min_price_cache)
        time.sleep(DELAY)

        season_str = f"{season}-{str(season+1)[-2:]}" if season else '?'
        cards_data.append({
            'name': player_name,
            'team': team,
            'season': season_str,
            'position': pos_str,
            'collection_name': collection_name,
            'collection_rayos': col_total_rayos,
            'card_rayos': card_rayos,
            'avg_price': avg_price,
            'num_sales': num_sales,
            'min_price': min_price,
            'asset_id': asset_id,
        })

    print(f"\r  ✅ {total} cartas consultadas" + " " * 40)

    # Estadísticas rápidas
    with_avg = sum(1 for c in cards_data if c['avg_price'] is not None)
    with_min = sum(1 for c in cards_data if c['min_price'] is not None)
    print(f"   {with_avg} con historial de ventas, {with_min} con ofertas en mercado")

    write_excel(cards_data)

    # Abrir el Excel en macOS
    import subprocess
    subprocess.Popen(['open', OUTPUT_PATH])


if __name__ == '__main__':
    main()
