import requests
import os
from openpyxl import Workbook
import re


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
    print("Error: JWT_TOKEN no encontrado en config.txt")
    exit(1)

print("JWT_TOKEN cargado correctamente")

headers = {
    'content-type': 'application/json',
    'Authorization': f'Bearer {JWT_TOKEN}',
    'JWT-AUD': JWT_AUD,
}

query = '''
query GetCards($first: Int!, $after: String) {
  currentUser {
    cards(first: $first, after: $after) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        assetId
        slug
        name
        rarityTyped
        seasonYear
      }
    }
  }
}
'''

def fetch_all_rare_cards():
    rare_cards = []
    cursor = None
    while True:
        variables = {
            'first': 50,
            'after': cursor,
        }
        response = requests.post(
            'https://api.sorare.com/graphql',
            json={'query': query, 'variables': variables},
            headers=headers
        )
        response.raise_for_status()
        data = response.json()['data']['currentUser']['cards']

        for card in data['nodes']:
            if card['rarityTyped'] == 'rare':
                rare_cards.append(card)

        if not data['pageInfo']['hasNextPage']:
            break
        cursor = data['pageInfo']['endCursor']

    return rare_cards

if __name__ == '__main__':
    rare_cards = fetch_all_rare_cards()
    print(f'Tienes {len(rare_cards)} cartas raras:')
    for card in rare_cards:
        print(f"- {card['name']} ({card['seasonYear']}), assetId: {card['assetId']}")

    # Crear carpeta output si no existe
    os.makedirs("../output", exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Rare Cards"

    # Cabeceras
    ws.append(['name', 'seasonYear', 'assetId'])

    # Filas
    for card in rare_cards:
        ws.append([
            card['name'],
            card['seasonYear'],
            card['assetId']
        ])

    wb.save("../output/rare_cards.xlsx")
    print("Excel generado: ../output/rare_cards.xlsx")