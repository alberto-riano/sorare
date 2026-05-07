#!/usr/bin/env python3
"""
Detector de chollos — La Liga (Rare + Limited + Classic)

Escanea ofertas de venta (Buy Now) de jugadores relevantes de La Liga,
compara con ventas recientes, otras ofertas activas, y cruza precios entre rarezas.
Puntúa cada oportunidad de 0 a 100.

Uso:
    python3 busqueda_chollos/detector_chollos.py
    python3 busqueda_chollos/detector_chollos.py --top 10
    python3 busqueda_chollos/detector_chollos.py --min-score 60
    python3 busqueda_chollos/detector_chollos.py --rarity rare
    python3 busqueda_chollos/detector_chollos.py --rarity classic --min-score 30
"""

import sys
import os
import time
import argparse
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

# Setup path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from sorare_utils import (
    graphql_request, build_headers, fetch_exchange_rates,
    get_live_single_sale_offers, get_recent_prices, to_eur_cents,
)

# ============================================================
# CONFIGURACIÓN
# ============================================================
MIN_SCORE_DEFAULT = 50          # Score mínimo para mostrar
TOP_N_DEFAULT = 20              # Máximo de chollos a mostrar
MOSTRAR_RAREZA = 'limited'         # Qué mostrar: 'rare', 'limited', 'classic', 'todas'
RARE_TO_LIMITED_RATIO = 4.0     # Ratio esperado rare/limited
CLASSIC_TO_LIMITED_RATIO = 2.0  # Ratio esperado classic/limited (aprox)
RECENCY_DAYS = 21               # Ventas de los últimos X días
SEASON_YEAR = 2025              # Temporada actual (para rare/limited)
# ============================================================

PLAYERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jugadores_la_liga.txt')


@dataclass
class DealCandidate:
    player_name: str
    player_slug: str
    offer_price_eur: float          # Precio de la oferta actual
    rarity: str                     # rare, limited, classic
    serial: int
    season: Optional[int]
    grade: Optional[float]
    asset_id: str
    # Análisis
    median_recent: Optional[float] = None
    weighted_avg: Optional[float] = None
    num_recent_sales: int = 0
    days_since_last_sale: Optional[int] = None
    other_offers_min: Optional[float] = None
    other_offers_count: int = 0
    cross_rarity_implied: Optional[float] = None   # Precio implícito desde otra rareza
    cross_rarity_label: str = ""                    # Ej: "limited×4", "rare÷4"
    # Score
    score: int = 0
    reasons: list = field(default_factory=list)


def load_players():
    """Carga la lista de slugs de jugadores del archivo."""
    players = []
    with open(PLAYERS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                players.append(line)
    return players


def get_offers_for_player(player_slug, headers, rates, rarities=None):
    """Obtiene ofertas de venta activas para un jugador, agrupadas por rareza."""
    if rarities is None:
        rarities = ['rare', 'limited', 'classic']
    offers = get_live_single_sale_offers(player_slug, headers=headers)
    by_rarity = {r: [] for r in rarities}

    for offer in offers:
        cards = offer['senderSide']['anyCards']
        for card in cards:
            rarity = card['rarityTyped']
            if rarity not in rarities:
                continue
            # Para rare/limited: solo temporada actual. Para classic: cualquiera.
            season = card.get('seasonYear')
            if rarity in ('rare', 'limited') and (season or 0) < SEASON_YEAR:
                continue
            amounts = offer['receiverSide']['amounts']
            eur_cents = to_eur_cents(amounts, rates)
            if eur_cents and eur_cents > 0:
                by_rarity[rarity].append({
                    'player_name': card['anyPlayer']['displayName'],
                    'player_slug': card['anyPlayer']['slug'],
                    'serial': card['serialNumber'],
                    'season': season,
                    'grade': card.get('grade'),
                    'asset_id': card['assetId'],
                    'price_eur': eur_cents / 100,
                    'rarity': rarity,
                    'team': card['anyTeam']['name'] if card['anyTeam'] else '?',
                })

    # Ordenar cada grupo por precio
    for r in by_rarity:
        by_rarity[r].sort(key=lambda x: x['price_eur'])
    return by_rarity


def get_weighted_avg_price(prices_data, rates):
    """
    Calcula precio medio ponderado por recencia.
    Ventas más recientes pesan más (decay exponencial).
    """
    if not prices_data:
        return None, None, 0, None

    now = datetime.now(timezone.utc)
    entries = []
    for p in prices_data:
        eur_cents = to_eur_cents(p['amounts'], rates)
        if not eur_cents or eur_cents <= 0:
            continue
        # Parsear fecha
        date_str = p.get('date', '')
        try:
            if 'T' in date_str:
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            dt = now - timedelta(days=30)  # fallback: antigua

        days_ago = (now - dt).days
        if days_ago > RECENCY_DAYS:
            continue  # Ignorar ventas demasiado antiguas
        entries.append({'eur': eur_cents / 100, 'days_ago': days_ago})

    if not entries:
        return None, None, 0, None

    # Peso: e^(-0.1 * days_ago) → venta de hoy = 1.0, de hace 7 días = 0.5, de hace 21 = 0.12
    total_weight = 0
    weighted_sum = 0
    for e in entries:
        weight = 2.718 ** (-0.1 * e['days_ago'])
        weighted_sum += e['eur'] * weight
        total_weight += weight

    weighted_avg = weighted_sum / total_weight if total_weight > 0 else None

    # Mediana simple
    prices_sorted = sorted(e['eur'] for e in entries)
    n = len(prices_sorted)
    median = prices_sorted[n // 2] if n % 2 == 1 else (prices_sorted[n // 2 - 1] + prices_sorted[n // 2]) / 2

    days_since_last = min(e['days_ago'] for e in entries)

    return weighted_avg, median, n, days_since_last


def calculate_score(candidate: DealCandidate) -> int:
    """
    Calcula un score de 0-100 basado en múltiples factores.
    Más alto = más chollo.
    """
    score = 0
    reasons = []
    price = candidate.offer_price_eur

    # === Factor 1: Descuento vs media ponderada reciente (0-35 puntos) ===
    if candidate.weighted_avg and candidate.weighted_avg > 0:
        discount_pct = (candidate.weighted_avg - price) / candidate.weighted_avg * 100
        if discount_pct > 0:
            points = min(35, int(discount_pct * 0.7))
            score += points
            reasons.append(f"-{discount_pct:.0f}% vs media reciente ({candidate.weighted_avg:.1f}€)")
        else:
            score += max(-20, int(discount_pct * 0.5))
            reasons.append(f"+{-discount_pct:.0f}% sobre media reciente")

    # === Factor 2: Posición respecto a otras ofertas activas (0-20 puntos) ===
    if candidate.other_offers_count > 0 and candidate.other_offers_min:
        if candidate.other_offers_min > price:
            gap_pct = (candidate.other_offers_min - price) / candidate.other_offers_min * 100
            points = min(20, int(gap_pct * 1.0))
            score += points
            reasons.append(f"Más barata: {price:.1f}€ vs siguiente {candidate.other_offers_min:.1f}€ ({candidate.other_offers_count} más)")
        else:
            score -= 10
            reasons.append(f"No es la más barata (hay a {candidate.other_offers_min:.1f}€)")

    # === Factor 3: Cruce entre rarezas (0-20 puntos) ===
    if candidate.cross_rarity_implied and candidate.cross_rarity_implied > 0:
        discount_vs_implied = (candidate.cross_rarity_implied - price) / candidate.cross_rarity_implied * 100
        if discount_vs_implied > 0:
            points = min(20, int(discount_vs_implied * 0.5))
            score += points
            reasons.append(f"{candidate.cross_rarity_label} sugiere {candidate.cross_rarity_implied:.1f}€")
        elif discount_vs_implied < -20:
            score -= 5
            reasons.append(f"Cara vs {candidate.cross_rarity_label}: implícito {candidate.cross_rarity_implied:.1f}€")

    # === Factor 4: Volumen y frescura de datos (0-15 puntos) ===
    if candidate.num_recent_sales >= 5:
        score += 10
        reasons.append(f"{candidate.num_recent_sales} ventas recientes (datos fiables)")
    elif candidate.num_recent_sales >= 3:
        score += 5
    elif candidate.num_recent_sales <= 1:
        score -= 5
        reasons.append("Pocos datos de ventas recientes")

    if candidate.days_since_last_sale is not None:
        if candidate.days_since_last_sale <= 2:
            score += 5
            reasons.append("Venta muy reciente (mercado activo)")
        elif candidate.days_since_last_sale > 14:
            score -= 5
            reasons.append(f"Última venta hace {candidate.days_since_last_sale} días (poca liquidez)")

    # === Factor 5: Nivel/Grade de la carta (0-15 puntos) ===
    # Cartas con nivel alto dan bonus SO5 → más valiosas de lo que parece
    grade = candidate.grade
    if grade is not None:
        if candidate.rarity == 'classic':
            # Classic con grade alto es MUY valioso (bonus SO5 significativo)
            if grade >= 10:
                score += 15
                reasons.append(f"⭐ Classic nivel {grade} (bonus SO5 máximo)")
            elif grade >= 9:
                score += 12
                reasons.append(f"⭐ Classic nivel {grade} (bonus SO5 alto)")
            elif grade >= 7:
                score += 5
                reasons.append(f"Classic nivel {grade}")
        else:
            # Rare/limited con grade alto también es bonus pero menor impacto relativo
            if grade >= 8:
                score += 8
                reasons.append(f"Nivel {grade} (bonus SO5)")
            elif grade >= 5:
                score += 3

    # === Factor 6: Serial bajo (bonus leve) ===
    if candidate.serial and candidate.serial <= 10:
        score += 5
        reasons.append(f"Serial bajo: #{candidate.serial}")

    # Clamp 0-100
    score = max(0, min(100, score))

    candidate.score = score
    candidate.reasons = reasons
    return score


def analyze_player(player_slug, headers, rates, mostrar_rareza=None):
    """
    Analiza todas las ofertas de un jugador.
    Siempre escanea TODAS las rarezas (para cruzar precios),
    pero solo devuelve candidatos de mostrar_rareza.
    """
    all_rarities = ['rare', 'limited', 'classic']
    if mostrar_rareza and mostrar_rareza != 'todas':
        output_rarities = [mostrar_rareza]
    else:
        output_rarities = all_rarities
    candidates = []

    # 1. Ofertas activas por rareza (TODAS, para poder cruzar)
    offers_by_rarity = get_offers_for_player(player_slug, headers, rates, all_rarities)

    # 2. Ventas recientes por rareza (TODAS, para cruzar precios)
    sales_data = {}  # {rarity: (weighted_avg, median, num_sales, days_since)}
    for rarity in all_rarities:
        try:
            # Classic: sin filtro de temporada. Rare/limited: temporada actual.
            season = SEASON_YEAR if rarity in ('rare', 'limited') else None
            recent = get_recent_prices(player_slug, rarity, season=season, headers=headers)
        except Exception:
            recent = []
        sales_data[rarity] = get_weighted_avg_price(recent, rates)

    # 3. Calcular precios implícitos cruzados
    # limited_median → implica rare = limited × 4, classic = limited × 2
    # rare_median → implica limited = rare / 4
    _, limited_median, _, _ = sales_data.get('limited', (None, None, 0, None))
    _, rare_median, _, _ = sales_data.get('rare', (None, None, 0, None))

    cross_implied = {}
    if limited_median:
        cross_implied['rare'] = (limited_median * RARE_TO_LIMITED_RATIO, f"limited×{RARE_TO_LIMITED_RATIO:.0f}")
        cross_implied['classic'] = (limited_median * CLASSIC_TO_LIMITED_RATIO, f"limited×{CLASSIC_TO_LIMITED_RATIO:.0f}")
    if rare_median:
        cross_implied['limited'] = (rare_median / RARE_TO_LIMITED_RATIO, f"rare÷{RARE_TO_LIMITED_RATIO:.0f}")

    # 4. Evaluar ofertas SOLO de las rarezas que queremos mostrar
    for rarity in output_rarities:
        rarity_offers = offers_by_rarity.get(rarity, [])
        if not rarity_offers:
            continue

        weighted_avg, median, num_sales, days_since = sales_data.get(rarity, (None, None, 0, None))
        implied_price, implied_label = cross_implied.get(rarity, (None, ""))

        for i, offer in enumerate(rarity_offers):
            other_min = rarity_offers[i + 1]['price_eur'] if i + 1 < len(rarity_offers) else None
            other_count = len(rarity_offers) - 1

            candidate = DealCandidate(
                player_name=offer['player_name'],
                player_slug=offer['player_slug'],
                offer_price_eur=offer['price_eur'],
                rarity=rarity,
                serial=offer['serial'],
                season=offer.get('season'),
                grade=offer.get('grade'),
                asset_id=offer['asset_id'],
                median_recent=median,
                weighted_avg=weighted_avg,
                num_recent_sales=num_sales,
                days_since_last_sale=days_since,
                other_offers_min=other_min,
                other_offers_count=other_count,
                cross_rarity_implied=implied_price,
                cross_rarity_label=implied_label,
            )
            calculate_score(candidate)
            candidates.append(candidate)

    return candidates


def print_deal_inline(d):
    """Imprime un chollo en tiempo real mientras se escanea."""
    if d.score >= 80:
        emoji = "🔥🔥"
    elif d.score >= 70:
        emoji = "🔥"
    elif d.score >= 60:
        emoji = "👀"
    else:
        emoji = "📊"

    rarity_tag = d.rarity.upper()
    grade_str = f"Lvl {int(d.grade)}" if d.grade else "?"
    print(f"\n   {emoji} {d.player_name} — {d.offer_price_eur:.2f}€ [{rarity_tag}] [SCORE: {d.score}]  #{d.serial} | {grade_str}")
    if d.weighted_avg:
        print(f"      Media: {d.weighted_avg:.2f}€ | ", end="")
    if d.cross_rarity_implied:
        print(f"Cruce ({d.cross_rarity_label}): {d.cross_rarity_implied:.2f}€ | ", end="")
    if d.other_offers_min:
        print(f"Siguiente: {d.other_offers_min:.2f}€", end="")
    print()


def print_ranking(deals, min_score, rarity_filter):
    """Imprime el ranking final ordenado por score."""
    rarity_label = rarity_filter.upper() if rarity_filter != 'todas' else "TODAS"
    print("\n" + "=" * 80)
    print(f"🏆 RANKING FINAL — La Liga {rarity_label}")
    print("=" * 80)
    print(f"   Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Score mínimo: {min_score}")
    print("=" * 80)

    if not deals:
        print(f"\n   No se encontraron chollos con score >= {min_score}")
        print("=" * 80 + "\n")
        return

    for i, d in enumerate(deals, 1):
        if d.score >= 80:
            emoji = "🔥🔥"
        elif d.score >= 70:
            emoji = "🔥"
        elif d.score >= 60:
            emoji = "👀"
        else:
            emoji = "📊"

        rarity_tag = d.rarity.upper()
        grade_str = f"Lvl {int(d.grade)}" if d.grade else "?"
        print(f"\n{emoji} #{i}  {d.player_name} — {d.offer_price_eur:.2f}€  [{rarity_tag}] [SCORE: {d.score}/100]")
        print(f"      Serial: #{d.serial} | Season: {d.season or 'classic'} | {grade_str}")
        print(f"      Asset: {d.asset_id[:30]}...")

        if d.weighted_avg:
            print(f"      📈 Media ponderada ({d.rarity}): {d.weighted_avg:.2f}€ ({d.num_recent_sales} ventas, última hace {d.days_since_last_sale}d)")
        if d.median_recent:
            print(f"      📊 Mediana ({d.rarity}): {d.median_recent:.2f}€")
        if d.cross_rarity_implied:
            print(f"      📉 Cruce rarezas ({d.cross_rarity_label}): implica ~{d.cross_rarity_implied:.2f}€")
        if d.other_offers_count > 0:
            print(f"      🏪 Otras ofertas: {d.other_offers_count} (siguiente: {d.other_offers_min:.2f}€)" if d.other_offers_min else f"      🏪 Es la única oferta")

        if d.reasons:
            print(f"      💡 {' | '.join(d.reasons[:4])}")

        print(f"      🔗 https://sorare.com/football/players/{d.player_slug}")

    print("\n" + "=" * 80)
    print(f"   Total chollos encontrados: {len(deals)}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description='Detector de chollos La Liga')
    parser.add_argument('--min-score', type=int, default=MIN_SCORE_DEFAULT, help=f'Score mínimo (default: {MIN_SCORE_DEFAULT})')
    parser.add_argument('--top', type=int, default=TOP_N_DEFAULT, help=f'Máximo resultados (default: {TOP_N_DEFAULT})')
    parser.add_argument('--player', type=str, default=None, help='Analizar solo un jugador (slug)')
    parser.add_argument('--rarity', type=str, default=None, choices=['rare', 'limited', 'classic', 'todas'],
                        help=f'Mostrar solo esta rareza (default: {MOSTRAR_RAREZA})')
    args = parser.parse_args()

    # Rareza a mostrar: argumento > config
    mostrar = args.rarity if args.rarity else MOSTRAR_RAREZA

    headers = build_headers()
    print("💱 Obteniendo tasas de cambio...")
    rates = fetch_exchange_rates()

    if args.player:
        players = [args.player]
    else:
        players = load_players()

    label = mostrar.upper() if mostrar != 'todas' else 'TODAS'
    print(f"🔍 Analizando {len(players)} jugadores — mostrando: {label}\n")

    all_deals = []
    for i, slug in enumerate(players, 1):
        print(f"   [{i}/{len(players)}] {slug}...", end="", flush=True)
        try:
            candidates = analyze_player(slug, headers, rates, mostrar_rareza=mostrar)
            good = [c for c in candidates if c.score >= args.min_score]
            if good:
                # Mostrar el mejor de este jugador en tiempo real
                best = max(good, key=lambda c: c.score)
                print(f" ✅ {len(good)} chollo(s)")
                print_deal_inline(best)
                if len(good) > 1:
                    print(f"      (+{len(good)-1} más)")
                all_deals.extend(good)
            else:
                print(f" —")
        except Exception as e:
            print(f" ❌ Error: {e}")

        # Rate limiting suave
        if i < len(players):
            time.sleep(0.5)

    # Ranking final ordenado por score
    all_deals.sort(key=lambda d: d.score, reverse=True)
    all_deals = all_deals[:args.top]

    print_ranking(all_deals, args.min_score, mostrar)


if __name__ == '__main__':
    main()
