#!/usr/bin/env python3
"""
Script para consultar el precio de mercado de cartas similares en Sorare.
Dado un assetId, busca todas las cartas del mismo jugador y rareza que están a la venta.
"""

from sorare_utils import (
    build_headers, fetch_exchange_rates, get_card_info,
    get_live_single_sale_offers, get_recent_prices,
    to_eur_cents, format_price, format_eur_equiv, format_eur,
    get_matching_offers,
)

# ============================================================
# CONFIGURACIÓN: Cambia el assetId de la carta que quieras consultar
# ============================================================
ASSET_ID = "0x04001efe727e6032cf81edae019cc577d9f740563d8b0b3acc105ab273c19756"
# Sergi Guardiola • Rare #5 (2022)
# ============================================================


def main():
    headers = build_headers()

    print("Obteniendo tasas de cambio actuales...")
    rates = fetch_exchange_rates()
    usd_to_eur, gbp_to_eur, eth_to_eur = rates
    print(f"  1 USD = {usd_to_eur:.4f} EUR | 1 GBP = {gbp_to_eur:.4f} EUR | 1 ETH = {eth_to_eur:.2f} EUR")

    print(f"\n{'=' * 70}")
    print(f"  SORARE - Consulta de precios de mercado de cartas similares")
    print(f"{'=' * 70}")
    print(f"\nAsset ID: {ASSET_ID}\n")

    # Paso 1: Obtener info de la carta
    print("Obteniendo información de la carta...")
    card = get_card_info(ASSET_ID, headers=headers)

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

    _, matching_offers = get_matching_offers(ASSET_ID, headers=headers, rates=rates)

    if matching_offers:
        print(f"Se encontraron {len(matching_offers)} cartas {rarity} de {player_name} a la venta:\n")
        print(f"  {'#':<4} {'Serial':<10} {'Temp.':<8} {'Nivel':<8} {'Precio':<16} {'(~EUR)':<12} {'Asset ID'}")
        print(f"  {'-'*4} {'-'*10} {'-'*8} {'-'*8} {'-'*16} {'-'*12} {'-'*34}")

        for i, o in enumerate(matching_offers, 1):
            price_str = format_price(o['amounts'])
            eur_str = format_eur_equiv(o['amounts'], rates)
            print(f"  {i:<4} #{o['serial']:<9} {o['season']:<8} {o['grade']:<8} {price_str:<16} {eur_str:<12} {o['asset_id'][:34]}")

        cheapest = matching_offers[0]
        print(f"\n  PRECIO MÍNIMO: {format_price(cheapest['amounts'])} (~{format_eur_equiv(cheapest['amounts'], rates)}) (Serial #{cheapest['serial']}, Temporada {cheapest['season']})")
    else:
        print(f"No se encontraron cartas {rarity} de {player_name} a la venta en el mercado.")

    # Paso 3: Últimas ventas realizadas
    print(f"\n{'=' * 70}")
    print(f"  Últimas ventas realizadas de {player_name} ({rarity})")
    print(f"{'=' * 70}\n")

    recent_prices = get_recent_prices(player_slug, rarity, season, headers=headers)

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

    if season:
        print(f"\n  --- Ventas de cualquier temporada ---\n")
        all_prices = get_recent_prices(player_slug, rarity, headers=headers)
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
