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
from web_services.direct_offer_market import player_in_season_listings  # noqa: E402
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
    if euros < Decimal("0.50") or euros > Decimal("10000"):
        return JsonResponse({"error": "La oferta debe estar entre 0,50 € y 10.000 €."}, status=400)
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