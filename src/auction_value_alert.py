#!/usr/bin/env python3
"""Avisa por Telegram de subastas Rare LaLiga In-Season próximas a terminar."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import html
import json
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from listar_subastas import load_auction_cache  # noqa: E402
from sorare_utils import build_headers, graphql_request, read_config, to_eur_cents  # noqa: E402
from web_services.config_files import DEFAULT_TELEGRAM_SETTINGS, parse_key_value_file  # noqa: E402
from web_services.opportunity_market import _floor_query, robust_sales_reference  # noqa: E402


SETTINGS_PATH = ROOT / "config" / "telegram_alert_settings.txt"
STATE_PATH = ROOT / "output" / "auction_value_alert_state.json"
VALUE_TTL = timedelta(hours=6)
SEASON_YEAR = 2026


def parse_date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def next_bid_eur(min_next_bid, currency, eth_eur_cents):
    """Convierte la cantidad mínima nativa de Sorare a euros."""
    try:
        raw = Decimal(str(min_next_bid))
    except (InvalidOperation, TypeError, ValueError):
        return None
    currency = str(currency or "").upper()
    if currency == "EUR":
        return float(raw / Decimal(100))
    if currency == "WEI" and eth_eur_cents:
        return float(raw * Decimal(str(eth_eur_cents)) / Decimal(10**18) / Decimal(100))
    return None


def candidate_auctions(rows, now, lead_minutes, already_checked):
    result = []
    for row in rows or []:
        auction_id = row.get("auction_id")
        end = parse_date(row.get("end_date"))
        if not auction_id or not end or auction_id in already_checked:
            continue
        remaining = (end - now).total_seconds()
        if 0 < remaining <= lead_minutes * 60:
            result.append(row)
    return result


def _live_details(headers, auction_ids):
    details, rate, nickname = {}, None, ""
    for offset in range(0, len(auction_ids), 20):
        batch = auction_ids[offset:offset + 20]
        variables, definitions, fields = {}, [], []
        for index, full_id in enumerate(batch):
            key = f"id{index}"
            variables[key] = full_id.replace("EnglishAuction:", "")
            definitions.append(f"${key}: String!")
            fields.append(f"""a{index}: auction(id: ${key}) {{
              id open endDate currentPrice currency minNextBid
              bestBid {{ userBidder {{ nickname }} }}
              anyCards {{ slug }}
            }}""")
        query = (
            "query AuctionAlert(" + ",".join(definitions) + ") { "
            "config { exchangeRate { ethRates { eurCents } } } "
            "currentUser { nickname } tokens { " + " ".join(fields) + " } }"
        )
        data = graphql_request(query, variables, headers=headers)
        tokens = data.get("tokens") or {}
        for index, auction_id in enumerate(batch):
            details[auction_id] = tokens.get(f"a{index}")
        rate = (((data.get("config") or {}).get("exchangeRate") or {}).get("ethRates") or {}).get("eurCents")
        nickname = ((data.get("currentUser") or {}).get("nickname") or "").strip()
    return details, rate, nickname


def _history_query(player_slug):
    return """
      query AuctionAlertHistory($slug: String!) {
        tokens {
          prices: tokenPrices(playerSlug: $slug, rarity: rare, season: 2026,
            seasonEligibility: IN_SEASON, first: 20) {
            amounts { eurCents usdCents gbpCents wei }
            date
            card { seasonYear inSeasonEligible }
            deal { __typename ... on TokenOffer { type } }
          }
        }
      }
    """, {"slug": player_slug}


def _valuation(player_slug, headers, rates, now):
    query, variables = _floor_query({"player_slug": player_slug})
    market = graphql_request(query, variables, headers=headers).get("player") or {}
    nodes = (market.get("rare") or {}).get("nodes") or []
    card = (nodes[0] if nodes else {}).get("lowestPriceCard") or {}
    offer = card.get("liveSingleSaleOffer") or {}
    floor_cents = to_eur_cents((offer.get("receiverSide") or {}).get("amounts") or {}, rates)
    floor = round(floor_cents / 100, 2) if floor_cents else None

    history_query, history_variables = _history_query(player_slug)
    prices = (graphql_request(history_query, history_variables, headers=headers).get("tokens") or {}).get("prices") or []
    cutoff = now - timedelta(days=30)
    comparables = []
    for price in prices:
        deal = price.get("deal") or {}
        typename = deal.get("__typename")
        is_public_offer = typename == "TokenOffer" and deal.get("type") == "SINGLE_BUY_OFFER"
        is_auction = typename == "TokenAuction"
        sold_at = parse_date(price.get("date"))
        card_data = price.get("card") or {}
        if not (is_public_offer or is_auction) or not sold_at or sold_at < cutoff:
            continue
        if card_data.get("seasonYear") != SEASON_YEAR or not card_data.get("inSeasonEligible"):
            continue
        cents = to_eur_cents(price.get("amounts") or {}, rates)
        if cents and cents > 0:
            comparables.append({"eur": round(cents / 100, 2), "date": price.get("date")})
    summary = robust_sales_reference(comparables, now=now)
    sales_reference = summary.get("value")
    values = [float(value) for value in (floor, sales_reference) if value is not None]
    return {
        "value": round(min(values), 2) if values else None,
        "floor": floor,
        "sales_reference": sales_reference,
        "sales_count": len(summary.get("sales") or []),
        "confidence": summary.get("confidence") or "low",
    }


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


def _send_telegram(token, chat_id, text):
    if not token or not chat_id:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram rechazó el mensaje")


def _message(row, detail, valuation, next_eur, saving, remaining_minutes):
    cards = detail.get("anyCards") or []
    card_slug = (cards[0] if cards else {}).get("slug")
    url = (
        "https://sorare.com/football/market/shop/auctions?card=" + card_slug
        if card_slug else "https://sorare.com/football/players/" + row.get("player_slug", "")
    )
    sources = []
    if valuation.get("floor") is not None:
        sources.append(f"suelo {valuation['floor']:.2f} €")
    if valuation.get("sales_reference") is not None:
        sources.append(f"ventas {valuation['sales_reference']:.2f} €")
    evidence = " · ".join(sources)
    return (
        "🚨 <b>Oportunidad de subasta</b>\n"
        f"<b>{html.escape(str(row.get('player') or 'Jugador'))}</b> · {html.escape(str(row.get('team') or 'LaLiga'))}\n"
        f"Siguiente puja: <b>{next_eur:.2f} €</b>\n"
        f"Valor estimado: <b>{valuation['value']:.2f} €</b> ({html.escape(evidence)})\n"
        f"Ahorro: <b>{saving:.1f}%</b> · quedan aprox. {max(1, remaining_minutes)} min\n"
        f"<a href=\"{html.escape(url, quote=True)}\">Abrir y pujar en Sorare</a>"
    )


def run(*, dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    settings = {**DEFAULT_TELEGRAM_SETTINGS, **parse_key_value_file(SETTINGS_PATH)}
    if settings.get("AUCTION_ALERT_ENABLED", "false").lower() != "true" and not dry_run:
        print("Alertas de subasta desactivadas.")
        return 0
    lead_minutes = int(settings.get("AUCTION_ALERT_MINUTES") or 3)
    min_saving = float(settings.get("AUCTION_ALERT_MIN_SAVING_PERCENT") or 20)
    rule = f"{lead_minutes}:{min_saving:.2f}"
    state = _load_state()
    if state.get("rule") != rule:
        state = {"rule": rule, "checked": {}, "values": {}}
    checked = state.setdefault("checked", {})
    values_cache = state.setdefault("values", {})
    cache = load_auction_cache() or {}
    candidates = candidate_auctions(cache.get("auctions"), now, lead_minutes, checked)
    if not candidates:
        print("No hay subastas nuevas dentro de la ventana de aviso.")
        return 0

    config = read_config()
    headers = build_headers(config)
    details, eth_rate, nickname = _live_details(headers, [row["auction_id"] for row in candidates])
    # La tasa ETH procede del mismo snapshot de Sorare que ``minNextBid``. Así
    # evitamos consultar servicios externos en cada ejecución del temporizador.
    rates = (0.92, 1.17, float(eth_rate or 0) / 100)
    sent = 0
    for row in candidates:
        auction_id = row["auction_id"]
        detail = details.get(auction_id) or {}
        end = parse_date(detail.get("endDate") or row.get("end_date"))
        if not detail.get("open") or not end or end <= now:
            checked[auction_id] = now.isoformat()
            continue
        winner = (((detail.get("bestBid") or {}).get("userBidder") or {}).get("nickname") or "").strip()
        if winner and nickname and winner.casefold() == nickname.casefold():
            checked[auction_id] = now.isoformat()
            continue
        next_eur = next_bid_eur(detail.get("minNextBid"), detail.get("currency"), eth_rate)
        if next_eur is None or next_eur <= 0:
            continue
        slug = row.get("player_slug")
        cached = values_cache.get(slug) or {}
        cached_at = parse_date(cached.get("calculated_at"))
        if not cached_at or now - cached_at > VALUE_TTL:
            cached = _valuation(slug, headers, rates, now)
            cached["calculated_at"] = now.isoformat()
            values_cache[slug] = cached
        value = cached.get("value")
        if not value:
            checked[auction_id] = now.isoformat()
            continue
        saving = (float(value) - next_eur) / float(value) * 100
        remaining_minutes = max(0, int((end - now).total_seconds() // 60))
        if saving >= min_saving:
            message = _message(row, detail, cached, next_eur, saving, remaining_minutes)
            if dry_run:
                print(message)
            else:
                _send_telegram(config.get("TELEGRAM_BOT_TOKEN"), config.get("TELEGRAM_CHAT_ID"), message)
            sent += 1
        checked[auction_id] = now.isoformat()

    cutoff = now - timedelta(days=2)
    state["checked"] = {key: value for key, value in checked.items() if (parse_date(value) or now) >= cutoff}
    if not dry_run:
        _save_state(state)
    print(f"Revisadas {len(candidates)} subastas; {sent} avisos {'simulados' if dry_run else 'enviados'}.")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:
        print(f"Error en alertas de subasta: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
