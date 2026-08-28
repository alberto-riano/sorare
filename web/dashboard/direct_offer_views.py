from __future__ import annotations

import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sorare_utils import build_headers, search_players_by_name  # noqa: E402
from web_services.config_files import SorarePaths  # noqa: E402
from web_services.direct_offer_market import check_direct_offer_eur, player_in_season_listings  # noqa: E402
from web_services.process_runner import run_direct_offer, sale_error_message  # noqa: E402


PATHS = SorarePaths(repo_root=REPO_ROOT)
PLAYER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,178}$")


def direct_offers(request):
    return render(request, "dashboard/direct_offers.html")


@require_GET
def direct_offer_player_search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"players": []})
    try:
        players = search_players_by_name(query, headers=build_headers())[:12]
    except (Exception, SystemExit) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"players": players})


@require_GET
def direct_offer_listings(request):
    player_slug = request.GET.get("player_slug", "").strip()
    if not PLAYER_SLUG_RE.fullmatch(player_slug):
        return JsonResponse({"error": "Jugador no válido."}, status=400)
    try:
        rows = player_in_season_listings(player_slug, headers=build_headers())
    except (Exception, SystemExit) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"listings": rows})


@require_POST
def preview_direct_offers(request):
    try:
        payload = json.loads(request.body or "{}")
        player_slug = str(payload.get("player_slug") or "").strip()
        euros = Decimal(str(payload.get("euros"))).quantize(Decimal("0.01"))
        requested = payload.get("offers") or []
    except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "Datos no válidos."}, status=400)
    if not PLAYER_SLUG_RE.fullmatch(player_slug) or euros < Decimal("0.01") or euros > Decimal("10000"):
        return JsonResponse({"error": "Jugador o importe no válido."}, status=400)
    if not isinstance(requested, list) or not requested or len(requested) > 50:
        return JsonResponse({"error": "Selecciona entre 1 y 50 cartas."}, status=400)

    headers = build_headers()
    try:
        listings = player_in_season_listings(player_slug, headers=headers)
    except (Exception, SystemExit) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    by_key = {(row["asset_id"], row["manager_slug"]): row for row in listings}
    amount_cents = int(euros * 100)
    results = []
    for item in requested:
        if not isinstance(item, dict):
            results.append({
                "asset_id": "", "manager_slug": "", "compatible": False,
                "error": "Datos de carta no válidos.",
            })
            continue
        asset_id = str((item or {}).get("asset_id") or "").strip()
        manager_slug = str((item or {}).get("manager_slug") or "").strip()
        listing = by_key.get((asset_id, manager_slug))
        result = {"asset_id": asset_id, "manager_slug": manager_slug, "compatible": False}
        if not listing:
            result["error"] = "La venta ya no está disponible para ese manager."
        else:
            result.update({
                "manager": listing["manager"],
                "serial_number": listing.get("serial_number"),
                "rarity": listing.get("rarity"),
            })
            try:
                errors = check_direct_offer_eur(
                    asset_id, manager_slug, amount_cents, headers=headers,
                )
                result["compatible"] = not errors
                if errors:
                    result["error"] = " · ".join(errors)[:500]
            except Exception as exc:
                result["error"] = str(exc)[:500]
        results.append(result)
    return JsonResponse({
        "currency": "EUR",
        "euros": str(euros),
        "results": results,
        "compatible_count": sum(1 for result in results if result["compatible"]),
    })


@require_POST
def create_direct_offer(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Datos no válidos."}, status=400)

    player_slug = str(payload.get("player_slug") or "").strip()
    asset_id = str(payload.get("asset_id") or "").strip()
    manager_slug = str(payload.get("manager_slug") or "").strip()
    try:
        euros = Decimal(str(payload.get("euros"))).quantize(Decimal("0.01"))
        duration_hours = int(payload.get("duration_hours", 48))
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "Importe o duración no válidos."}, status=400)

    if not PLAYER_SLUG_RE.fullmatch(player_slug) or not asset_id or not manager_slug:
        return JsonResponse({"error": "Faltan datos de la carta o del manager."}, status=400)
    if euros < Decimal("0.01") or euros > Decimal("10000"):
        return JsonResponse({"error": "La oferta debe estar entre 0,01 € y 10.000 €."}, status=400)
    if duration_hours not in {24, 48, 72, 168}:
        return JsonResponse({"error": "Duración no válida."}, status=400)

    try:
        rows = player_in_season_listings(player_slug, headers=build_headers())
    except (Exception, SystemExit) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    listing = next(
        (row for row in rows if row["asset_id"] == asset_id and row["manager_slug"] == manager_slug),
        None,
    )
    if not listing:
        return JsonResponse({"error": "La carta ya no está a la venta por ese manager."}, status=409)

    result = run_direct_offer(
        PATHS,
        asset_id=asset_id,
        manager_slug=manager_slug,
        euros=str(euros),
        duration_hours=duration_hours,
    )
    if result.exit_code != 0:
        return JsonResponse({"error": sale_error_message(result)}, status=502)
    return JsonResponse({"ok": True, "asset_id": asset_id, "manager": listing["manager"]})