#!/usr/bin/env python3
"""
Script SIMPLE - Solo muestra las cartas RARE que están EN LINEUP
"""

import requests
import json

SORARE_API_URL = "https://api.sorare.com/graphql"


def read_config(config_path="../config.txt"):
    """Lee las variables del archivo config.txt"""
    config = {}
    with open(config_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    return config


# Leer configuración
config = read_config()
JWT_TOKEN = config.get('JWT_TOKEN')
JWT_AUD = config.get('JWT_AUD', 'myapp')

if not JWT_TOKEN:
    print("❌ Error: JWT_TOKEN no encontrado en config.txt")
    exit(1)

print("✅ JWT_TOKEN cargado correctamente\n")

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {JWT_TOKEN}",
    "JWT-AUD": JWT_AUD
}

print("🔍 Buscando cartas RARE en alineaciones...\n")


# Función para obtener TODAS las cartas con paginación
def get_all_rare_cards():
    all_cards = []
    has_next_page = True
    cursor = None
    page = 1

    while has_next_page:
        print(f"📥 Cargando página {page}...", end="\r")

        # Query con paginación
        query = f"""
        query GetCardsPage {{
          currentUser {{
            slug
            nickname
            cards(rarities: [rare], first: 100{f', after: "{cursor}"' if cursor else ''}) {{
              nodes {{
                assetId
                name
                slug
                rarityTyped
                seasonYear
                serialNumber
                grade
                inSeasonEligible
                anyPlayer {{
                  displayName
                }}
                anyTeam {{
                  name
                }}
                anyPositions
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

        response = requests.post(
            SORARE_API_URL,
            headers=headers,
            json={"query": query}
        )

        if response.status_code != 200:
            print(f"\n❌ Error HTTP: {response.status_code}")
            print(response.text)
            exit(1)

        data = response.json()

        if "errors" in data:
            print(f"\n❌ Error GraphQL:")
            print(json.dumps(data['errors'], indent=2))
            exit(1)

        user_data = data.get("data", {}).get("currentUser", {})
        cards_data = user_data.get("cards", {})

        cards = cards_data.get("nodes", [])
        all_cards.extend(cards)

        page_info = cards_data.get("pageInfo", {})
        has_next_page = page_info.get("hasNextPage", False)
        cursor = page_info.get("endCursor")

        page += 1

        # Protección contra loops infinitos
        if page > 50:  # Máximo 50 páginas = 5000 cartas
            print("\n⚠️  Alcanzado límite de páginas")
            break

    print(f"\n✅ Cargadas {len(all_cards)} cartas RARE en total")

    return all_cards, user_data.get("blockchainCardsInLineups", []), user_data.get("nickname")


# Obtener todas las cartas
all_cards, lineup_slugs, username = get_all_rare_cards()
lineup_slugs = set(lineup_slugs)

# Filtrar solo cartas en lineup
cards_in_lineup = [card for card in all_cards if card.get("slug") in lineup_slugs]

if not cards_in_lineup:
    print("❌ No tienes cartas RARE en alineaciones activas")
    exit(0)

print(f"\n{'=' * 100}")
print(f"✅ Usuario: {username}")
print(f"📊 Total cartas RARE: {len(all_cards)}")
print(f"🎮 Cartas RARE en lineup: {len(cards_in_lineup)}")
print(f"{'=' * 100}\n")

print("🟥 CARTAS RARE EN ALINEACIÓN")

# Ordenar por posición y nombre
cards_in_lineup.sort(key=lambda x: (
    x.get('anyPositions', ['ZZZ'])[0],
    x.get('anyPlayer', {}).get('displayName', '')
))

for i, card in enumerate(cards_in_lineup, 1):
    player_name = card.get('anyPlayer', {}).get('displayName', 'Unknown')
    team_name = card.get('anyTeam', {}).get('name', 'Unknown')
    positions = card.get('anyPositions', [])
    position_str = positions[0] if positions else 'N/A'

    grade = card.get('grade')
    grade_str = f"{grade:.1f}" if grade else "Sin calificar"

    serial = card.get('serialNumber')
    season = card.get('seasonYear')
    eligible = "✅" if card.get('inSeasonEligible') else "❌"

    print(f"\n{i}. {player_name} ({position_str})")
    print(f"   Equipo: {team_name}")
    print(f"   Carta: {card.get('name')}")
    print(f"   Grade: {grade_str} | Serial: {serial}/100 | Temporada: {season} | Elegible: {eligible}")
    print(f"   Slug: {card.get('slug')}")
    print(f"   Asset ID: {card.get('assetId')}")
    print("-" * 100)

print(f"\n{'=' * 100}")
print(f"📋 RESUMEN POR POSICIÓN")
print(f"{'=' * 100}")

# Contar por posición
by_position = {}
for card in cards_in_lineup:
    positions = card.get('anyPositions', [])
    pos = positions[0] if positions else 'Unknown'
    by_position[pos] = by_position.get(pos, 0) + 1

for pos in ['Goalkeeper', 'Defender', 'Midfielder', 'Forward']:
    if pos in by_position:
        print(f"   {pos}: {by_position[pos]} carta(s)")

print(f"\n{'=' * 100}")
print("✅ Análisis completado!")
print(f"{'=' * 100}\n")

# LISTA DE ASSET IDs
print(f"{'=' * 100}")
print(f"📋 LISTA DE ASSET IDs DE CARTAS EN LINEUP ({len(cards_in_lineup)})")
print(f"{'=' * 100}\n")

asset_ids = [card.get('assetId') for card in cards_in_lineup]

# Formato de lista Python para copiar/pegar fácilmente
print("# Lista en formato Python:")
print("asset_ids = [")
for asset_id in asset_ids:
    print(f'    "{asset_id}",')
print("]\n")

# Formato de lista simple (uno por línea)
print("# Lista simple (uno por línea):")
for asset_id in asset_ids:
    print(asset_id)

print(f"\n{'=' * 100}\n")