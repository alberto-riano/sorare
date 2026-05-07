import requests
import os
from openpyxl import Workbook


def read_config(config_path=None):
    """Lee las variables del archivo config.txt"""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'config.txt')
    config_map = {}
    with open(config_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
                config_map[key.strip()] = value.strip()
    return config_map


# Leer configuración
cfg = read_config()
JWT_TOKEN = cfg.get('JWT_TOKEN')
JWT_AUD = cfg.get('JWT_AUD', 'myapp')

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
    rare_cards_list = []
    cursor = None
    while True:
        variables = {
            'first': 50,
            'after': cursor,
        }
        response = requests.post(
            'https://api.sorare.com/graphql',
            json={'query': query, 'variables': variables},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()['data']['currentUser']['cards']

        for node in data['nodes']:
            if node['rarityTyped'] == 'rare':
                rare_cards_list.append(node)

        if not data['pageInfo']['hasNextPage']:
            break
        cursor = data['pageInfo']['endCursor']

    return rare_cards_list

if __name__ == '__main__':
    all_rare_cards = fetch_all_rare_cards()
    print(f'Tienes {len(all_rare_cards)} cartas raras:')
    for c in all_rare_cards:
        print(f"- {c['name']} ({c['seasonYear']}), assetId: {c['assetId']}")

    # Crear carpeta output si no existe
    os.makedirs("../output", exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Rare Cards"

    # Cabeceras
    ws.append(['name', 'seasonYear', 'assetId'])

    # Filas
    for c in all_rare_cards:
        ws.append([
            c['name'],
            c['seasonYear'],
            c['assetId']
        ])

    wb.save("../output/rare_cards.xlsx")
    print("Excel generado: ../output/rare_cards.xlsx")