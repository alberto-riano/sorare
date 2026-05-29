#!/usr/bin/env python3
"""
Lista las ofertas directas recibidas (que puedo aceptar) en mis cartas.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sorare_utils import graphql_request, build_headers, fetch_exchange_rates


GET_PENDING_OFFERS_RECEIVED_QUERY = '''
{
  currentUser {
    pendingTokenOffersReceived(first: 50) {
      totalCount
      nodes {
        id
        status
        endDate
        createdAt
        sender { ... on User { slug nickname } }
        senderSide {
          amounts { wei eurCents }
          anyCards { slug assetId name rarityTyped seasonYear grade anyPlayer { displayName } }
        }
        receiverSide {
          amounts { wei eurCents }
          anyCards { slug assetId name rarityTyped seasonYear grade anyPlayer { displayName } }
        }
      }
    }
  }
}
'''


def wei_to_eth(wei_str):
    if not wei_str:
        return 0.0
    return int(wei_str) / 1e18


def parse_card(c):
    season = c.get('seasonYear', 0)
    season_display = f"{str(season)[2:]}-{str(season+1)[2:]}" if season else "?"
    level = c.get('grade', 0)
    return {
        'slug': c.get('slug', ''),
        'asset_id': c.get('assetId', ''),
        'name': c.get('name', ''),
        'player': c.get('anyPlayer', {}).get('displayName', 'Unknown'),
        'rarity': c.get('rarityTyped', 'unknown'),
        'season': season,
        'season_display': season_display,
        'level': level,
        'min_price_classic': None,
        'min_price_inseason': None,
    }


def fetch_pending_offers_received():
    """Obtiene todas las ofertas recibidas pendientes con conversión real ETH/EUR."""
    headers = build_headers()
    
    # Get real ETH/EUR rate
    _, _, eth_to_eur = fetch_exchange_rates()
    
    result = graphql_request(GET_PENDING_OFFERS_RECEIVED_QUERY, headers=headers)
    if not result:
        return [], eth_to_eur

    data = result.get('currentUser', {}).get('pendingTokenOffersReceived', {})
    nodes = data.get('nodes', [])

    offers = []
    for node in nodes:
        my_cards = [parse_card(c) for c in node.get('receiverSide', {}).get('anyCards', [])]
        their_cards = [parse_card(c) for c in node.get('senderSide', {}).get('anyCards', [])]

        # Money they send me (senderSide.amounts)
        s_wei = node.get('senderSide', {}).get('amounts', {}).get('wei')
        s_eur_cents = node.get('senderSide', {}).get('amounts', {}).get('eurCents')

        # Money they ask from me (receiverSide.amounts)
        r_wei = node.get('receiverSide', {}).get('amounts', {}).get('wei')
        r_eur_cents = node.get('receiverSide', {}).get('amounts', {}).get('eurCents')

        eth_they_send = wei_to_eth(s_wei)
        eur_they_send = (s_eur_cents / 100.0) if s_eur_cents else 0.0

        eth_they_ask = wei_to_eth(r_wei)
        eur_they_ask = (r_eur_cents / 100.0) if r_eur_cents else 0.0

        # Cross-calculate using real rate
        if eth_they_send > 0 and eur_they_send == 0:
            eur_they_send = eth_they_send * eth_to_eur
        elif eur_they_send > 0 and eth_they_send == 0:
            eth_they_send = eur_they_send / eth_to_eur

        if eth_they_ask > 0 and eur_they_ask == 0:
            eur_they_ask = eth_they_ask * eth_to_eur
        elif eur_they_ask > 0 and eth_they_ask == 0:
            eth_they_ask = eur_they_ask / eth_to_eur

        # Determine offer type
        has_money_for_me = eth_they_send > 0 or eur_they_send > 0
        asks_money_from_me = eth_they_ask > 0 or eur_they_ask > 0
        has_their_cards = len(their_cards) > 0
        has_my_cards = len(my_cards) > 0

        # Check if receiverSide has None amounts (API doesn't expose price)
        r_wei_raw = node.get('receiverSide', {}).get('amounts', {}).get('wei')
        r_eur_raw = node.get('receiverSide', {}).get('amounts', {}).get('eurCents')
        receiver_price_unknown = (r_wei_raw is None and r_eur_raw is None)

        if has_my_cards and has_their_cards:
            if receiver_price_unknown:
                offer_type = 'swap'  # Card swap, may also involve hidden money
            else:
                offer_type = 'swap'
        elif has_money_for_me and has_my_cards:
            offer_type = 'buy'  # Someone buying my card with money
        elif has_their_cards and asks_money_from_me:
            offer_type = 'sell'  # Someone selling me their card for money
        elif has_their_cards and receiver_price_unknown:
            offer_type = 'sell'  # Cards offered, price hidden by API
        elif has_money_for_me:
            offer_type = 'buy'
        else:
            offer_type = 'other'

        # Sort price: net value (positive = I gain money, negative = I pay)
        sort_price_eur = eur_they_send - eur_they_ask

        offer = {
            'id': node.get('id', ''),
            'status': node.get('status', ''),
            'endDate': node.get('endDate', ''),
            'createdAt': node.get('createdAt', ''),
            'sender_slug': node.get('sender', {}).get('slug', 'unknown'),
            'sender_nickname': node.get('sender', {}).get('nickname', 'Unknown'),
            'eth_they_send': eth_they_send,
            'eur_they_send': eur_they_send,
            'eth_they_ask': eth_they_ask,
            'eur_they_ask': eur_they_ask,
            'offer_type': offer_type,
            'sort_price_eur': sort_price_eur,
            'price_unknown': receiver_price_unknown and (has_their_cards or (has_my_cards and has_their_cards)),
            'my_cards': my_cards,
            'their_cards': their_cards,
        }
        offers.append(offer)

    # Sort by price descending
    offers.sort(key=lambda o: o['sort_price_eur'], reverse=True)
    return offers, eth_to_eur


if __name__ == "__main__":
    print("🔍 Buscando ofertas recibidas...")
    offers, rate = fetch_pending_offers_received()
    print(f"   (ETH/EUR rate: €{rate:.2f})")
    if not offers:
        print("✅ No tienes ofertas pendientes.")
    else:
        print(f"\n📬 {len(offers)} ofertas:\n")
        for i, o in enumerate(offers, 1):
            my = ", ".join(c['name'] for c in o['my_cards']) or "—"
            their = ", ".join(c['name'] for c in o['their_cards']) or "—"
            print(f"  {i}. [{o['offer_type']}] {o['sender_nickname']}")
            print(f"     Quiere: {my}")
            print(f"     Ofrece: {their}")
            if o['eth_they_send'] > 0: print(f"     💰 Me paga: Ξ{o['eth_they_send']:.4f} / €{o['eur_they_send']:.2f}")
            if o['eth_they_ask'] > 0: print(f"     💸 Me pide: Ξ{o['eth_they_ask']:.4f} / €{o['eur_they_ask']:.2f}")
            print()
