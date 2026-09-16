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
REPEAT_ALERT_WINDOW = timedelta(hours=48)

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


def _offer_version_key(offer):
    """Identifica una versión del anuncio, no solo la oferta.

    Sorare puede conservar el mismo id al modificar un precio. Incluir la fecha
    de actualización y los importes permite volver a valorar una rebaja sin
    repetir avisos por el solapamiento entre ejecuciones.
    """
    amounts = (offer.get("receiverSide") or {}).get("amounts") or {}
    amount_signature = ":".join(str(amounts.get(key) or "") for key in ("eurCents", "usdCents", "gbpCents", "wei"))
    version = offer.get("updatedAt") or offer.get("startDate") or ""
    return f"{offer.get('id')}:{version}:{amount_signature}"


def _alert_price_key(offer):
    """Agrupa avisos por jugador y rareza, no por cada refresco de Sorare."""
    card = _card_from_offer(offer)
    player_slug = (card.get("anyPlayer") or {}).get("slug") or ""
    rarity = card.get("rarityTyped") or "rare"
    return f"{player_slug}:{rarity}"


def _repeat_price_alert(notified_prices, channel, offer, price, now):
    """Evita repetir durante 48 h el mismo precio (o uno peor).

    Sorare puede cambiar ``updatedAt`` aunque el anuncio siga igual. Si baja el
    precio sí vuelve a ser una señal nueva y, por tanto, se deja pasar.
    """
    entry = ((notified_prices.get(channel) or {}).get(_alert_price_key(offer)) or {})
    alerted_at = _parse_date(entry.get("at"))
    try:
        alerted_price = float(entry.get("price"))
    except (TypeError, ValueError):
        return False
    return bool(alerted_at and now - alerted_at < REPEAT_ALERT_WINDOW and price >= alerted_price)


def _remember_price_alert(notified_prices, channel, offer, price, now):
    notified_prices.setdefault(channel, {})[_alert_price_key(offer)] = {
        "price": round(float(price), 2), "at": now.isoformat(),
    }


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


def _player_valuation(candidate, headers, rates, now, ratio, ratio_source, snapshot_row=None, weights=None):
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
    snapshot_row = snapshot_row or {}
    cached_limited = snapshot_row.get("limited") or {}
    cached_rare = snapshot_row.get("rare") or {}
    cached_limited_value = cached_limited.get("market_value") or cached_limited.get("sales_reference")
    cached_rare_reference = cached_rare.get("sales_reference")
    limited_reference = limited_summary.get("value") or cached_limited_value
    rare_reference = rare_summary.get("value") or cached_rare_reference
    rare_confidence = rare_summary.get("confidence")
    if not rare_summary.get("value") and cached_rare_reference:
        rare_confidence = cached_rare.get("confidence") or "low"
    limited_values = [value for value in (limited_floor, limited_reference) if value]
    limited_value = min(limited_values) if limited_values else None
    parity = limited_value * ratio if limited_value else None
    weights = weights or {"sales": 3, "parity": 2, "floor": 1}
    fair_value = estimate_fair_value(
        sales_reference=rare_reference,
        parity_reference=parity,
        market_floor_reference=rare_peer_floor,
        sales_confidence=rare_confidence,
        ratio_source=ratio_source,
        sales_weight=weights["sales"],
        parity_weight=weights["parity"],
        floor_weight=weights["floor"],
    )
    cached_rare_sales = cached_rare.get("sales") or []
    comparable_count = len(rare_summary.get("sales") or [])
    if not rare_summary.get("value"):
        comparable_count = len(cached_rare_sales)
    return {
        "fair_value": fair_value,
        "limited_value": round(limited_value, 2) if limited_value else None,
        "limited_floor": limited_floor,
        "rare_peer_floor": rare_peer_floor,
        "rare_sales_reference": rare_reference,
        "rare_sales_count": comparable_count,
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


def _message(offer, price, valuation, saving, *, premium=False):
    card = _card_from_offer(offer)
    player = card.get("anyPlayer") or {}
    team = card.get("anyTeam") or {}
    parts = []
    if valuation.get("rare_peer_floor"):
        parts.append(f"suelo alternativo {valuation['rare_peer_floor']:.2f} €")
    if valuation.get("rare_sales_reference"):
        parts.append(f"ventas/pujas {valuation['rare_sales_reference']:.2f} €")
    if valuation.get("limited_value"):
        parity = valuation.get("parity_reference") or (valuation["limited_value"] * valuation["ratio"])
        parts.append(
            f"equiv. Rare por Limited {valuation['limited_value']:.2f} € × {valuation['ratio']:.2f} = {parity:.2f} €"
        )
    url = f"https://sorare.com/football/cards/{card.get('slug')}" if card.get("slug") else f"https://sorare.com/football/players/{player.get('slug', '')}"
    rarity_icon = "🔵" if card.get("rarityTyped") == "super_rare" else "🔴"
    return (
        f"{rarity_icon} <b>{html.escape(str(player.get('displayName') or 'Jugador'))}</b>\n"
        f"{html.escape(str(team.get('name') or 'LaLiga'))} · In-Season\n\n"
        f"Precio: <b>{price:.2f} €</b> · Valor: <b>{valuation['fair_value']:.2f} €</b> · Ahorro: <b>{saving:.1f}%</b>\n"
        f"{html.escape(' · '.join(parts))}\n\n"
        f"<a href=\"{html.escape(url, quote=True)}\">Ver carta y comprar</a>"
    )


def run(*, dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    settings = {**DEFAULT_TELEGRAM_SETTINGS, **parse_key_value_file(SETTINGS_PATH)}
    market_enabled = settings.get("MARKET_ALERT_ENABLED", "false").lower() == "true"
    bargain_enabled = settings.get("BARGAIN_ALERT_ENABLED", "false").lower() == "true"
    if not market_enabled and not bargain_enabled and not dry_run:
        print("Alertas de nuevas ventas desactivadas.")
        return 0
    min_saving = float(settings.get("MARKET_ALERT_MIN_SAVING_PERCENT") or 25)
    min_limited = float(settings.get("MARKET_ALERT_MIN_LIMITED_VALUE_EUR") or 1)
    min_comparables = int(settings.get("MARKET_ALERT_MIN_COMPARABLES") or 0)
    bargain_min_saving = float(settings.get("BARGAIN_ALERT_MIN_SAVING_PERCENT") or 40)
    weights = {
        "sales": int(settings.get("VALUATION_SALES_WEIGHT") or 3),
        "parity": int(settings.get("VALUATION_PARITY_WEIGHT") or 2),
        "floor": int(settings.get("VALUATION_FLOOR_WEIGHT") or 1),
    }
    state = _load_state()
    settings_signature = f"{min_saving}:{min_limited}:{min_comparables}:{bargain_enabled}:{bargain_min_saving}:{weights}"
    settings_changed = state.get("settings_signature") not in (None, settings_signature)
    last_run = _parse_date(state.get("last_run_at"))
    updated_after = (last_run - timedelta(minutes=OVERLAP_MINUTES)) if last_run else (now - timedelta(minutes=POLL_INTERVAL_MINUTES + OVERLAP_MINUTES))
    updated_after = max(updated_after, now - timedelta(days=8))

    opportunity_rows, ratio, ratio_source = _opportunity_context()
    config = read_config()
    premium_ready = bargain_enabled and bool(config.get("TELEGRAM_BARGAIN_CHAT_ID"))
    headers = build_headers(config)
    rates = fetch_exchange_rates()
    offers = _fetch_recent_listings(headers, updated_after)
    candidates = [offer for offer in offers if _matching_listing(offer, opportunity_rows)]
    seen = {} if settings_changed else state.setdefault("seen", {})
    pending = state.setdefault("pending", {})
    notified_prices = state.setdefault("notified_prices", {})
    candidates_by_id = {
        _offer_version_key(offer): offer for offer in [*pending.values(), *candidates]
        if offer.get("id") and _offer_version_key(offer) not in seen and _matching_listing(offer, opportunity_rows)
    }
    new_candidates = list(candidates_by_id.values())
    sent = bargain_sent = evaluated = valued = failures = filtered = below_threshold = repeat_suppressed = 0
    errors = []
    evaluations = []
    for candidate in new_candidates:
        version_key = _offer_version_key(candidate)
        player = (_card_from_offer(candidate).get("anyPlayer") or {}).get("displayName") or "Jugador"
        try:
            price = _offer_eur(candidate, rates)
            if not price:
                seen[version_key] = now.isoformat()
                pending.pop(version_key, None)
                filtered += 1
                evaluations.append({"player": player, "decision": "Precio no convertible a EUR"})
                continue
            card = _card_from_offer(candidate)
            normal_already_sent = market_enabled and _repeat_price_alert(
                notified_prices, "market", candidate, price, now,
            )
            bargain_already_sent = premium_ready and _repeat_price_alert(
                notified_prices, "bargain", candidate, price, now,
            )
            # Si todas las conversaciones que podrían recibirlo ya lo vieron a
            # este precio (o a uno mejor), no gastamos dos consultas extra en
            # revalorar el mismo anuncio actualizado por Sorare.
            active_channels = int(market_enabled) + int(premium_ready)
            repeated_channels = int(normal_already_sent) + int(bargain_already_sent)
            if active_channels and active_channels == repeated_channels:
                seen[version_key] = now.isoformat()
                pending.pop(version_key, None)
                filtered += 1
                repeat_suppressed += 1
                evaluations.append({"player": player, "price": price, "decision": "Ya avisada a este precio o uno inferior (<48 h)"})
                continue
            slug = (card.get("anyPlayer") or {}).get("slug")
            snapshot_row = opportunity_rows.get(slug) or {}
            valuation = _player_valuation(candidate, headers, rates, now, ratio, ratio_source, snapshot_row, weights)
            evaluated += 1
            fair_value = valuation.get("fair_value")
            if fair_value:
                valued += 1
            saving = ((fair_value - price) / fair_value * 100) if fair_value and fair_value > price else 0
            decision = "Aviso enviado"
            eligible = True
            if valuation.get("limited_value") is None:
                decision, eligible = "Sin valoración Limited", False
            elif valuation["limited_value"] < min_limited:
                decision, eligible = f"Limited por debajo de {min_limited:.2f} €", False
            elif valuation.get("rare_sales_count", 0) < min_comparables:
                decision, eligible = f"Solo {valuation.get('rare_sales_count', 0)} comparables", False
            elif not fair_value:
                decision, eligible = "Sin datos para calcular valor justo", False
            elif fair_value <= price:
                decision, eligible = "Sin ahorro frente al valor justo", False
            elif saving < min_saving:
                decision, eligible = f"Ahorro inferior al {min_saving:g}%", False
                below_threshold += 1
            if eligible and ((market_enabled and saving >= min_saving) or (premium_ready and saving >= bargain_min_saving)):
                if market_enabled and saving >= min_saving and not normal_already_sent:
                    message = _message(candidate, price, valuation, saving)
                    if dry_run:
                        print(message)
                    else:
                        _send_telegram(config.get("TELEGRAM_BOT_TOKEN"), config.get("TELEGRAM_CHAT_ID"), message, (card.get("anyPlayer") or {}).get("squaredPictureUrl"))
                    sent += 1
                    _remember_price_alert(notified_prices, "market", candidate, price, now)
                if premium_ready and saving >= bargain_min_saving and not bargain_already_sent:
                    premium_message = _message(candidate, price, valuation, saving, premium=True)
                    if dry_run:
                        print(premium_message)
                    else:
                        _send_telegram(
                            config.get("TELEGRAM_BOT_TOKEN"), config.get("TELEGRAM_BARGAIN_CHAT_ID"), premium_message,
                            (card.get("anyPlayer") or {}).get("squaredPictureUrl"),
                        )
                    bargain_sent += 1
                    _remember_price_alert(notified_prices, "bargain", candidate, price, now)
                if normal_already_sent or bargain_already_sent:
                    decision = "Aviso repetido omitido: mismo precio durante 48 h"
            else:
                filtered += 1
            evaluations.append({
                "player": player, "price": price, "fair_value": fair_value,
                "saving_percent": round(saving, 1), "limited_value": valuation.get("limited_value"),
                "comparables": valuation.get("rare_sales_count", 0), "decision": decision,
            })
            seen[version_key] = now.isoformat()
            pending.pop(version_key, None)
        except Exception as exc:
            failures += 1
            pending[version_key] = candidate
            errors.append(str(exc)[:180])
            evaluations.append({"player": player, "decision": "Error al valorar", "error": str(exc)[:180]})
        time.sleep(REQUEST_INTERVAL_SECONDS)

    cutoff = now - timedelta(days=9)
    state["seen"] = {key: value for key, value in seen.items() if (_parse_date(value) or now) >= cutoff}
    state["pending"] = pending
    notified_cutoff = now - REPEAT_ALERT_WINDOW
    state["notified_prices"] = {
        channel: {
            key: entry for key, entry in prices.items()
            if (_parse_date((entry or {}).get("at")) or now) >= notified_cutoff
        }
        for channel, prices in notified_prices.items() if isinstance(prices, dict)
    }
    state.update({
        "settings_signature": settings_signature,
        "last_run_at": now.isoformat(), "last_result": "partial" if failures else "ok", "offers_scanned": len(offers),
        "new_candidates": len(new_candidates), "evaluated_count": evaluated, "valued_count": valued,
        "filtered_count": filtered, "below_threshold_count": below_threshold, "alerts_sent": sent,
        "bargain_alerts_sent": bargain_sent,
        "repeat_suppressed_count": repeat_suppressed,
        "max_saving_percent": max((row.get("saving_percent") or 0 for row in evaluations), default=0),
        "last_evaluations": evaluations[-8:], "failure_count": failures, "last_errors": errors[-3:],
    })
    if not dry_run:
        _save_state(state)
    print(f"{len(offers)} ofertas recientes; {len(new_candidates)} Rare nuevas/modificadas; {valued} valoradas; {sent} avisos {'simulados' if dry_run else 'enviados'}; {failures} pendientes.")
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
