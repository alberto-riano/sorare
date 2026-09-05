#!/usr/bin/env python3
"""Detecta nuevas ventas Rare In-Season de LaLiga con descuento relevante."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "web"))

from sorare_utils import (  # noqa: E402
    build_headers, fetch_exchange_rates, get_live_single_sale_offers,
    graphql_request, read_config, to_eur_cents,
)
from web_services.config_files import DEFAULT_TELEGRAM_SETTINGS, parse_key_value_file  # noqa: E402
from web_services.opportunity_market import (  # noqa: E402
    FALLBACK_RARE_RATIO, estimate_fair_value, robust_sales_reference,
)


SETTINGS_PATH = ROOT / "config" / "telegram_alert_settings.txt"
STATE_PATH = ROOT / "output" / "market_listing_alert_state.json"
SEASON_YEAR = 2026
POLL_INTERVAL_MINUTES = 30
OVERLAP_MINUTES = 3
MAX_HISTORY_DAYS = 45
REQUEST_INTERVAL_SECONDS = 1.05

RECENT_LISTINGS_QUERY = """
query RecentMarketListings($updatedAfter: ISO8601DateTime!, $first: Int!, $after: String) {
  tokens {
    liveSingleSaleOffers(sport: FOOTBALL, updatedAfter: $updatedAfter, first: $first, after: $after) {
      nodes {
        id startDate updatedAt endDate type
        receiverSide { amounts { eurCents usdCents gbpCents wei } }
        senderSide {
          anyCards {
            assetId slug pictureUrl rarityTyped seasonYear serialNumber inSeasonEligible
            anyPlayer { slug displayName squaredPictureUrl }
            anyTeam { name }
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

HISTORY_QUERY = """
query ListingAlertHistory($slug: String!) {
  tokens {
    limited: tokenPrices(playerSlug: $slug, rarity: limited, season: 2026,
      seasonEligibility: IN_SEASON, first: 20) {
      amounts { eurCents usdCents gbpCents wei } date
      card { seasonYear inSeasonEligible }
      deal { __typename ... on TokenOffer { type } }
    }
    rare: tokenPrices(playerSlug: $slug, rarity: rare, season: 2026,
      seasonEligibility: IN_SEASON, first: 20) {
      amounts { eurCents usdCents gbpCents wei } date
      card { seasonYear inSeasonEligible }
      deal { __typename ... on TokenOffer { type } }
    }
  }
}
"""


def _parse_date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_PATH)


def _opportunity_context():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sorare_web.settings")
    import django
    django.setup()
    from dashboard.models import OpportunitySnapshot

    snapshot = OpportunitySnapshot.objects.filter(market_key="laliga-2026").first()
    if not snapshot or not snapshot.rows:
        raise RuntimeError("No hay un análisis de Oportunidades disponible. Actualízalo primero.")
    rows = {row.get("player_slug"): row for row in snapshot.rows if row.get("player_slug")}
    metadata = snapshot.metadata or {}
    ratio = float(metadata.get("rare_limited_ratio") or FALLBACK_RARE_RATIO)
    ratio_source = metadata.get("ratio_source") or "fallback"
    return rows, ratio, ratio_source


def _fetch_recent_listings(headers, updated_after):
    rows, cursor = [], None
    while True:
        data = graphql_request(RECENT_LISTINGS_QUERY, {
            "updatedAfter": updated_after.isoformat(), "first": 50, "after": cursor,
        }, headers=headers)
        connection = (data.get("tokens") or {}).get("liveSingleSaleOffers") or {}
        rows.extend(connection.get("nodes") or [])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return rows


def _card_from_offer(offer):
    cards = (offer.get("senderSide") or {}).get("anyCards") or []
    return cards[0] if len(cards) == 1 else {}


def _offer_eur(offer, rates):
    cents = to_eur_cents((offer.get("receiverSide") or {}).get("amounts") or {}, rates)
    return round(cents / 100, 2) if cents and cents > 0 else None


def _matching_listing(offer, player_slugs):
    card = _card_from_offer(offer)
    player_slug = (card.get("anyPlayer") or {}).get("slug")
    return bool(
        offer.get("id") and player_slug in player_slugs
        and card.get("rarityTyped") == "rare"
        and card.get("seasonYear") == SEASON_YEAR
        and card.get("inSeasonEligible")
    )


def _comparable_sales(prices, rates, now):
    cutoff = now - timedelta(days=MAX_HISTORY_DAYS)
    result = []
    for price in prices or []:
        deal = price.get("deal") or {}
        comparable = (
            deal.get("__typename") == "TokenAuction"
            or (deal.get("__typename") == "TokenOffer" and deal.get("type") == "SINGLE_BUY_OFFER")
        )
        card = price.get("card") or {}
        sold_at = _parse_date(price.get("date"))
        if not comparable or not sold_at or sold_at < cutoff:
            continue
        if card.get("seasonYear") != SEASON_YEAR or not card.get("inSeasonEligible"):
            continue
        cents = to_eur_cents(price.get("amounts") or {}, rates)
        if cents and cents > 0:
            result.append({"eur": round(cents / 100, 2), "date": price.get("date")})
    return result


def _player_valuation(candidate, headers, rates, now, ratio, ratio_source):
    card = _card_from_offer(candidate)
    player_slug = (card.get("anyPlayer") or {}).get("slug")
    current_offers = get_live_single_sale_offers(player_slug, headers=headers)
    floors = {"limited": [], "rare": []}
    for offer in current_offers:
        offer_card = _card_from_offer(offer)
        rarity = offer_card.get("rarityTyped")
        if rarity not in floors or offer_card.get("seasonYear") != SEASON_YEAR or not offer_card.get("inSeasonEligible"):
            continue
        if rarity == "rare" and offer.get("id") == candidate.get("id"):
            continue
        price = _offer_eur(offer, rates)
        if price:
            floors[rarity].append(price)
    limited_floor = min(floors["limited"], default=None)
    rare_peer_floor = min(floors["rare"], default=None)

    time.sleep(REQUEST_INTERVAL_SECONDS)
    histories = (graphql_request(HISTORY_QUERY, {"slug": player_slug}, headers=headers).get("tokens") or {})
    limited_summary = robust_sales_reference(_comparable_sales(histories.get("limited"), rates, now), now=now)
    rare_summary = robust_sales_reference(_comparable_sales(histories.get("rare"), rates, now), now=now)
    limited_values = [value for value in (limited_floor, limited_summary.get("value")) if value]
    limited_value = min(limited_values) if limited_values else None
    parity = limited_value * ratio if limited_value else None
    fair_value = estimate_fair_value(
        sales_reference=rare_summary.get("value"),
        parity_reference=parity,
        market_floor_reference=rare_peer_floor,
        sales_confidence=rare_summary.get("confidence"),
        ratio_source=ratio_source,
    )
    return {
        "fair_value": fair_value,
        "limited_value": round(limited_value, 2) if limited_value else None,
        "limited_floor": limited_floor,
        "rare_peer_floor": rare_peer_floor,
        "rare_sales_reference": rare_summary.get("value"),
        "rare_sales_count": len(rare_summary.get("sales") or []),
        "parity_reference": round(parity, 2) if parity else None,
        "ratio": ratio,
    }


def _telegram_post(token, method, payload):
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or "Telegram rechazó el mensaje")


def _send_telegram(token, chat_id, text, photo_url=None):
    if not token or not chat_id:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    if photo_url:
        try:
            _telegram_post(token, "sendPhoto", {"chat_id": chat_id, "photo": photo_url, "caption": text, "parse_mode": "HTML"})
            return
        except (requests.RequestException, RuntimeError):
            pass
    _telegram_post(token, "sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False})


def _message(offer, price, valuation, saving):
    card = _card_from_offer(offer)
    player = card.get("anyPlayer") or {}
    team = card.get("anyTeam") or {}
    parts = []
    if valuation.get("rare_peer_floor"):
        parts.append(f"suelo alternativo {valuation['rare_peer_floor']:.2f} €")
    if valuation.get("rare_sales_reference"):
        parts.append(f"ventas/pujas {valuation['rare_sales_reference']:.2f} €")
    if valuation.get("limited_value"):
        parts.append(f"Limited {valuation['limited_value']:.2f} € × {valuation['ratio']:.2f}")
    url = f"https://sorare.com/football/cards/{card.get('slug')}" if card.get("slug") else f"https://sorare.com/football/players/{player.get('slug', '')}"
    return (
        f"🆕🔴 <b>Nueva oportunidad Rare</b>\n"
        f"<b>{html.escape(str(player.get('displayName') or 'Jugador'))}</b> · {html.escape(str(team.get('name') or 'LaLiga'))}\n\n"
        f"Precio: <b>{price:.2f} €</b> · Valor: <b>{valuation['fair_value']:.2f} €</b> · Ahorro: <b>{saving:.1f}%</b>\n"
        f"{html.escape(' · '.join(parts))}\n\n"
        f"<a href=\"{html.escape(url, quote=True)}\">Ver carta y comprar</a>"
    )


def run(*, dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    settings = {**DEFAULT_TELEGRAM_SETTINGS, **parse_key_value_file(SETTINGS_PATH)}
    if settings.get("MARKET_ALERT_ENABLED", "false").lower() != "true" and not dry_run:
        print("Alertas de nuevas ventas desactivadas.")
        return 0
    min_saving = float(settings.get("MARKET_ALERT_MIN_SAVING_PERCENT") or 25)
    min_limited = float(settings.get("MARKET_ALERT_MIN_LIMITED_VALUE_EUR") or 1)
    min_comparables = int(settings.get("MARKET_ALERT_MIN_COMPARABLES") or 0)
    state = _load_state()
    last_run = _parse_date(state.get("last_run_at"))
    updated_after = (last_run - timedelta(minutes=OVERLAP_MINUTES)) if last_run else (now - timedelta(minutes=POLL_INTERVAL_MINUTES + OVERLAP_MINUTES))
    updated_after = max(updated_after, now - timedelta(days=8))

    opportunity_rows, ratio, ratio_source = _opportunity_context()
    config = read_config()
    headers = build_headers(config)
    rates = fetch_exchange_rates()
    offers = _fetch_recent_listings(headers, updated_after)
    candidates = [offer for offer in offers if _matching_listing(offer, opportunity_rows)]
    seen = state.setdefault("seen", {})
    pending = state.setdefault("pending", {})
    candidates_by_id = {
        offer.get("id"): offer for offer in [*pending.values(), *candidates]
        if offer.get("id") and offer.get("id") not in seen and _matching_listing(offer, opportunity_rows)
    }
    new_candidates = list(candidates_by_id.values())
    sent = evaluated = failures = 0
    errors = []
    for candidate in new_candidates:
        offer_id = candidate["id"]
        try:
            price = _offer_eur(candidate, rates)
            if not price:
                seen[offer_id] = now.isoformat()
                pending.pop(offer_id, None)
                continue
            card = _card_from_offer(candidate)
            slug = (card.get("anyPlayer") or {}).get("slug")
            cached_limited = ((opportunity_rows.get(slug) or {}).get("limited") or {}).get("market_value")
            if cached_limited is not None and float(cached_limited) < min_limited:
                seen[offer_id] = now.isoformat()
                pending.pop(offer_id, None)
                continue
            valuation = _player_valuation(candidate, headers, rates, now, ratio, ratio_source)
            evaluated += 1
            fair_value = valuation.get("fair_value")
            eligible = (
                valuation.get("limited_value") is not None
                and valuation["limited_value"] >= min_limited
                and valuation.get("rare_sales_count", 0) >= min_comparables
                and fair_value and fair_value > price
            )
            saving = ((fair_value - price) / fair_value * 100) if eligible else 0
            if eligible and saving >= min_saving:
                message = _message(candidate, price, valuation, saving)
                if dry_run:
                    print(message)
                else:
                    _send_telegram(config.get("TELEGRAM_BOT_TOKEN"), config.get("TELEGRAM_CHAT_ID"), message, (card.get("anyPlayer") or {}).get("squaredPictureUrl"))
                sent += 1
            seen[offer_id] = now.isoformat()
            pending.pop(offer_id, None)
        except Exception as exc:
            failures += 1
            pending[offer_id] = candidate
            errors.append(str(exc)[:180])
        time.sleep(REQUEST_INTERVAL_SECONDS)

    cutoff = now - timedelta(days=9)
    state["seen"] = {key: value for key, value in seen.items() if (_parse_date(value) or now) >= cutoff}
    state["pending"] = pending
    state.update({
        "last_run_at": now.isoformat(), "last_result": "partial" if failures else "ok", "offers_scanned": len(offers),
        "new_candidates": len(new_candidates), "evaluated_count": evaluated, "alerts_sent": sent,
        "failure_count": failures, "last_errors": errors[-3:],
    })
    if not dry_run:
        _save_state(state)
    print(f"{len(offers)} ofertas recientes; {len(new_candidates)} Rare nuevas; {evaluated} valoradas; {sent} avisos {'simulados' if dry_run else 'enviados'}; {failures} pendientes.")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:
        if not args.dry_run:
            try:
                state = _load_state()
                state.update({"last_error_at": datetime.now(timezone.utc).isoformat(), "last_result": "error", "last_error": str(exc)[:500]})
                _save_state(state)
            except Exception:
                pass
        print(f"Error en alertas de nuevas ventas: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
