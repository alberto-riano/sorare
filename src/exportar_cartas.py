#!/usr/bin/env python3
"""Exporta cartas a Excel filtrando por rareza.

Ejemplos:
  python3 src/exportar_cartas.py
  python3 src/exportar_cartas.py --rarity limited
  python3 src/exportar_cartas.py --azules
"""

import argparse
import os
from openpyxl import Workbook

from sorare_utils import build_headers, graphql_request

RARITY_ALIASES = {
  "azules": "super_rare",
    "amarillas": "limited",
    "rojas": "rare",
}

CARDS_QUERY = '''
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


def normalize_rarity(raw_rarity: str) -> str:
    key = (raw_rarity or "").strip().lower()
    return RARITY_ALIASES.get(key, key)


def fetch_all_cards_by_rarity(*, rarity: str, headers: dict) -> list[dict]:
    cards: list[dict] = []
    cursor = None

    while True:
        data = graphql_request(CARDS_QUERY, {"first": 50, "after": cursor}, headers=headers)
        connection = data["currentUser"]["cards"]

        for node in connection["nodes"]:
            if (node.get("rarityTyped") or "").lower() == rarity:
                cards.append(node)

        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]

    return cards


def output_file_for_rarity(rarity: str) -> str:
    return os.path.join("..", "output", f"{rarity}_cards.xlsx")


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta cartas de Sorare por rareza a Excel")
    parser.add_argument(
        "--rarity",
        default="rare",
        choices=["limited", "rare", "super_rare", "unique", "azules", "amarillas", "rojas"],
      help="Rareza a exportar. Usa 'super_rare' o alias 'azules' para rareza azul.",
    )
    parser.add_argument("--azules", action="store_true", help="Atajo para exportar rareza azul (super_rare)")

    args = parser.parse_args()

    rarity = "super_rare" if args.azules else normalize_rarity(args.rarity)
    headers = build_headers()

    all_cards = fetch_all_cards_by_rarity(rarity=rarity, headers=headers)
    print(f"Tienes {len(all_cards)} cartas de rareza '{rarity}':")
    for card in all_cards:
        print(f"- {card['name']} ({card['seasonYear']}), assetId: {card['assetId']}")

    os.makedirs("../output", exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = f"{rarity.title()} Cards"
    ws.append(["name", "seasonYear", "assetId"])

    for card in all_cards:
        ws.append([card["name"], card["seasonYear"], card["assetId"]])

    output_file = output_file_for_rarity(rarity)
    wb.save(output_file)
    print(f"Excel generado: {output_file}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())