from __future__ import annotations

import sys
import uuid
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from io import BytesIO
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse

from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from web_services.config_files import SorarePaths, load_telegram_alert_payload, save_telegram_alert_payload
from web_services.process_runner import BidRequest, run_bid_scheduler, run_telegram_alert
from web_services.movement_history import build_trade_cycles
from web_services.sales_inventory import collection_display_name
from .forms import BatchBidForm, BatchSaleForm, BidScheduleForm, InlineBidForm, TelegramSettingsForm
from .models import (
    AuctionFilterPreset, AuctionRefreshJob, BidBatchItem, BidBatchJob, FavoritePlayer,
    MovementSnapshot, MovementSyncJob, PublicRewardSnapshot, PublicRewardSyncJob,
    OpportunityRefreshJob, OpportunitySnapshot, SaleBatchItem, SaleBatchJob,
    SalesInventory, SalesRefreshJob,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PATHS = SorarePaths(repo_root=REPO_ROOT)
SALES_SELECTED_RARITY_SESSION_KEY = "sales_selected_rarity"
PUBLIC_REWARD_MANAGER_SLUG = "blasco93"

# Añadir el directorio src al path para importar listar_subastas
sys.path.insert(0, str(REPO_ROOT / 'src'))

from web_services import token_service  # noqa: E402  (requiere src en sys.path)

OTP_CHALLENGE_SESSION_KEY = "sorare_otp_challenge"


def healthz(_request):
    """Sonda interna para systemd/deploy; no consulta ni expone la cuenta."""
    return HttpResponse("ok", content_type="text/plain")


def _to_bool_text(value: bool) -> str:
    return "true" if value else "false"


def index(request):
    cards = [
        {
            "title": "Alertas Telegram",
            "icon": "fas fa-bell",
            "description": "Configura players, umbrales y ejecuta alertas sin tocar archivos.",
            "url": "telegram_alerts",
            "status": "Disponible",
        },
        {
            "title": "Programar Puja",
            "icon": "fas fa-gavel",
            "description": "Lanza pujas now, sniper o por hora con formulario guiado.",
            "url": "bid_scheduler",
            "status": "Disponible",
        },
        {
            "title": "Exportar Cartas",
            "icon": "fas fa-tag",
            "description": "Exporta por rareza, edita precios y procesa ventas desde la web.",
            "url": "sales_workbench",
            "status": "Disponible",
        },
        {
            "title": "Subastas La Liga",
            "icon": "fas fa-gavel",
            "description": "Visualiza las subastas activas de La Liga en tiempo real.",
            "url": "auctions_list",
            "status": "Disponible",
        },
        {
            "title": "Oportunidades",
            "icon": "fas fa-gem",
            "description": "Compara suelos y ventas reales Limited/Rare para detectar desajustes de precio.",
            "url": "opportunities",
            "status": "Disponible",
        },
        {
            "title": "Ofertas Recibidas",
            "icon": "fas fa-envelope-open-text",
            "description": "Consulta las ofertas pendientes que has recibido por tus cartas.",
            "url": "offers_received",
            "status": "Disponible",
        },
        {
            "title": "Movimientos",
            "icon": "fas fa-chart-pie",
            "description": "Analiza compras, ventas, comisiones y recompensas de tu cuenta.",
            "url": "movements",
            "status": "Disponible",
        },
        {
            "title": "Renovar Token",
            "icon": "fas fa-key",
            "description": "Renueva el JWT con tu MFA cuando caduque, sin tocar archivos.",
            "url": "refresh_token",
            "status": "Disponible",
        },
        {
            "title": "Lineup Helper",
            "icon": "fas fa-users",
            "description": "Siguiente fase: asistente visual para alinear y comparar opciones.",
            "url": None,
            "status": "Proxima iteracion",
        },
    ]
    return render(request, "dashboard/index.html", {"cards": cards})


def opportunities(request):
    snapshot = OpportunitySnapshot.objects.filter(market_key="laliga-2026", source_version=1).first()
    active_refresh = OpportunityRefreshJob.objects.filter(
        status__in=(OpportunityRefreshJob.Status.QUEUED, OpportunityRefreshJob.Status.RUNNING),
    ).order_by("-created_at").first()
    if not snapshot and not active_refresh:
        active_refresh = OpportunityRefreshJob.objects.create(user=request.user)

    all_rows = list(snapshot.rows if snapshot else [])
    player_filter = request.GET.get("player", "").strip().casefold()
    team_filter = request.GET.get("team", "").strip()
    position_filter = request.GET.get("position", "").strip()
    rarity_filter = request.GET.get("opportunity_rarity", "").strip().lower()
    confidence_filter = request.GET.get("confidence", "").strip().lower()
    try:
        min_discount = max(0, min(95, int(request.GET.get("min_discount", "12"))))
    except ValueError:
        min_discount = 12
    show_all = request.GET.get("show_all") == "1"

    rows = []
    for row in all_rows:
        if player_filter and player_filter not in str(row.get("player") or "").casefold():
            continue
        if team_filter and row.get("team") != team_filter:
            continue
        if position_filter and row.get("position") != position_filter:
            continue
        if rarity_filter in {"limited", "rare"} and row.get("recommended_rarity") != rarity_filter:
            continue
        if confidence_filter in {"medium", "high"} and row.get("confidence") != confidence_filter:
            continue
        if not show_all and (not row.get("recommended_rarity") or float(row.get("discount_percent") or 0) < min_discount):
            continue
        rows.append(row)

    sort = request.GET.get("sort", "discount")
    if sort == "player":
        rows.sort(key=lambda row: str(row.get("player") or "").casefold())
    elif sort == "floor":
        rows.sort(key=lambda row: float((row.get(row.get("recommended_rarity") or "limited") or {}).get("floor") or 10**9))
    else:
        rows.sort(key=lambda row: float(row.get("discount_percent") or 0), reverse=True)
    try:
        per_page = int(request.GET.get("per_page", 25))
    except ValueError:
        per_page = 25
    per_page = per_page if per_page in {25, 50, 100} else 25
    page_obj = Paginator(rows, per_page).get_page(request.GET.get("page", 1))
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    metadata = snapshot.metadata if snapshot else {}
    return render(request, "dashboard/opportunities.html", {
        "snapshot": snapshot,
        "metadata": metadata,
        "active_refresh": active_refresh,
        "page_obj": page_obj,
        "total_market_rows": len(all_rows),
        "filtered_count": len(rows),
        "available_teams": sorted({row.get("team") for row in all_rows if row.get("team")}),
        "available_positions": sorted({row.get("position") for row in all_rows if row.get("position")}),
        "selected_team": team_filter,
        "selected_position": position_filter,
        "selected_rarity": rarity_filter,
        "selected_confidence": confidence_filter,
        "selected_min_discount": min_discount,
        "show_all": show_all,
        "sort": sort,
        "per_page": per_page,
        "pagination_query": pagination_params.urlencode(),
    })


@require_POST
def enqueue_opportunity_refresh(request):
    active = OpportunityRefreshJob.objects.filter(
        status__in=(OpportunityRefreshJob.Status.QUEUED, OpportunityRefreshJob.Status.RUNNING),
    ).order_by("-created_at").first()
    job = active or OpportunityRefreshJob.objects.create(user=request.user)
    return JsonResponse({"job_id": job.id, "status": job.status}, status=202)


@require_GET
def opportunity_refresh_status(request):
    jobs = OpportunityRefreshJob.objects.order_by("-created_at")[:5]
    return JsonResponse({"jobs": [{
        "id": job.id,
        "status": job.status,
        "processed_count": job.processed_count,
        "total_count": job.total_count,
        "percent": round(job.processed_count / job.total_count * 100) if job.total_count else 0,
        "player_count": job.player_count,
        "opportunity_count": job.opportunity_count,
        "progress_label": job.progress_label,
        "error": job.error,
    } for job in jobs]})


def _movement_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _movement_main_cards(row):
    """Cartas que definen la naturaleza económica de una operación.

    En un intercambio, las cartas recibidas como contraprestación no cambian
    el tipo de la carta vendida, y viceversa.
    """
    cash_direction = row.get("cash_direction") or row.get("direction")
    if cash_direction == "sale":
        return row.get("sent_cards") or row.get("cards") or []
    if cash_direction == "purchase":
        return row.get("received_cards") or row.get("cards") or []
    return row.get("cards") or []


def _movement_seasonality(row):
    cards = _movement_main_cards(row)
    if cards and all(bool(card.get("in_season")) for card in cards):
        return "inseason"
    if cards and all(not bool(card.get("in_season")) for card in cards):
        return "classic"
    return "mixed"


def movements(request):
    category = request.GET.get("category", "all")
    if category not in {"laliga_inseason", "reward", "other", "all"}:
        category = "all"
    selected_manager = request.GET.get("manager", "me") if category == "reward" else "me"
    if selected_manager not in {"me", PUBLIC_REWARD_MANAGER_SLUG}:
        selected_manager = "me"
    public_rewards = selected_manager == PUBLIC_REWARD_MANAGER_SLUG

    if public_rewards:
        stored_snapshot = PublicRewardSnapshot.objects.filter(manager_slug=selected_manager).first()
        snapshot = stored_snapshot if stored_snapshot and stored_snapshot.source_version >= 1 else None
        active_sync = PublicRewardSyncJob.objects.filter(
            manager_slug=selected_manager,
            status__in=(PublicRewardSyncJob.Status.QUEUED, PublicRewardSyncJob.Status.RUNNING),
        ).order_by("-created_at").first()
        if not snapshot and not active_sync:
            active_sync = PublicRewardSyncJob.objects.create(
                user=request.user,
                manager_slug=selected_manager,
            )
        manager_nickname = snapshot.manager_nickname if snapshot else "Blasco93"
    else:
        stored_snapshot = MovementSnapshot.objects.filter(user=request.user).first()
        snapshot = stored_snapshot if stored_snapshot and stored_snapshot.source_version >= 14 else None
        active_sync = MovementSyncJob.objects.filter(
            user=request.user,
            status__in=(MovementSyncJob.Status.QUEUED, MovementSyncJob.Status.RUNNING),
        ).order_by("-created_at").first()
        if not snapshot and not active_sync:
            active_sync = MovementSyncJob.objects.create(user=request.user)
        manager_nickname = "burguis"

    all_movements = list(snapshot.movements if snapshot else [])
    direction = request.GET.get("direction", "")
    if category == "reward" or direction not in {"", "purchase", "sale", "trade"}:
        direction = ""
    allowed_rarities = {"limited", "rare", "super_rare", "unique"}
    rarity = request.GET.get("rarity", "")
    if rarity not in allowed_rarities:
        rarity = ""
    reward_type = request.GET.get("reward_type", "")
    if category != "reward" or reward_type not in {"money", "essence", "card"}:
        reward_type = ""
    seasonality = request.GET.get("seasonality", "")
    if category == "reward" or seasonality not in {"inseason", "classic"}:
        seasonality = ""
    requested_date_from = request.GET.get("date_from")
    date_from = "2026-08-12" if requested_date_from is None else requested_date_from.strip()
    date_to = request.GET.get("date_to", "").strip()

    prepared_rows = []
    rarities = set()
    for raw in all_movements:
        row = dict(raw)
        cards = row.get("cards") or []
        rarities.update(card.get("rarity") for card in cards if card.get("rarity") in allowed_rarities)
        essence = row.get("essence") or []
        rarities.update(item.get("rarity") for item in essence if item.get("rarity") in allowed_rarities)
        if row.get("direction") == "reward":
            row["reward_type"] = row.get("reward_type") or (
                "money" if row.get("gross_eur") or row.get("eth")
                else "essence" if row.get("essence_quantity")
                else "card" if cards
                else ""
            )
            row["reward_rarity"] = row.get("reward_rarity") or (
                str((essence[0] if essence else {}).get("rarity") or "")
                or str((cards[0] if cards else {}).get("rarity") or "")
            )
        occurred_at = _movement_datetime(row.get("occurred_at"))
        row["occurred_at_dt"] = occurred_at
        prepared_rows.append(row)

    def in_selected_window(row):
        occurred_at = row.get("occurred_at_dt")
        local_occurred_at = occurred_at.astimezone(ZoneInfo("Europe/Madrid")) if occurred_at else None
        iso_date = local_occurred_at.date().isoformat() if local_occurred_at else ""
        if date_from and iso_date < date_from:
            return False
        if date_to and iso_date > date_to:
            return False
        return True

    # El inicio del intervalo es el estado de partida: una adquisición anterior
    # no debe arrastrar su coste ni formar un ciclo con movimientos posteriores.
    cycle_source_rows = [row for row in prepared_rows if in_selected_window(row)]

    rows = []
    for row in prepared_rows:
        cards = row.get("cards") or []
        main_cards = _movement_main_cards(row)
        essence = row.get("essence") or []
        occurred_at = row.get("occurred_at_dt")
        if category == "all" and row.get("category") == "reward":
            continue
        if category != "all" and row.get("category") != category:
            continue
        if direction and row.get("direction") != direction and row.get("cash_direction") != direction:
            continue
        if reward_type and row.get("reward_type") != reward_type:
            continue
        if rarity and not (
            any(card.get("rarity") == rarity for card in main_cards)
            or any(item.get("rarity") == rarity for item in essence)
        ):
            continue
        if seasonality == "inseason" and _movement_seasonality(row) != "inseason":
            continue
        if seasonality == "classic" and _movement_seasonality(row) != "classic":
            continue
        if not in_selected_window(row):
            continue
        rows.append(row)

    cycles = []
    if not direction and category != "reward":
        for cycle in build_trade_cycles(cycle_source_rows):
            cycle_cards = [cycle.get("purchase_card") or {}, cycle.get("sale_card") or {}]
            cycle_at = _movement_datetime(cycle.get("occurred_at"))
            if category != "all" and cycle.get("category") != category:
                continue
            if rarity and not any(card.get("rarity") == rarity for card in cycle_cards):
                continue
            principal_card = cycle.get("sale_card") or cycle.get("purchase_card") or {}
            if seasonality == "inseason" and not bool(principal_card.get("in_season")):
                continue
            if seasonality == "classic" and bool(principal_card.get("in_season")):
                continue
            local_cycle_at = cycle_at.astimezone(ZoneInfo("Europe/Madrid")) if cycle_at else None
            cycle_date = local_cycle_at.date().isoformat() if local_cycle_at else ""
            if date_from and cycle_date < date_from:
                continue
            if date_to and cycle_date > date_to:
                continue
            cycles.append(cycle)

    consumed_movement_ids = set()
    for cycle in cycles:
        cycle["row_type"] = "cycle"
        cycle["purchase_at_dt"] = _movement_datetime(cycle.get("purchase_at"))
        cycle["sale_at_dt"] = _movement_datetime(cycle.get("sale_at"))
        cycle["occurred_at_dt"] = _movement_datetime(cycle.get("occurred_at"))
        consumed_movement_ids.update(str(value) for value in cycle.get("movement_ids") or [] if value)
    for row in rows:
        row["row_type"] = "movement"

    display_rows = cycles + [
        row for row in rows
        if str(row.get("id") or "") not in consumed_movement_ids
    ]
    display_rows.sort(
        key=lambda row: row.get("occurred_at_dt") or datetime.min.replace(tzinfo=ZoneInfo("UTC")),
        reverse=True,
    )

    summary_rows_by_id = {str(row.get("id") or id(row)): row for row in rows}
    prepared_rows_by_id = {str(row.get("id") or ""): row for row in cycle_source_rows if row.get("id")}
    for movement_id in consumed_movement_ids:
        if movement_id in prepared_rows_by_id:
            summary_rows_by_id[movement_id] = prepared_rows_by_id[movement_id]
    summary_rows = list(summary_rows_by_id.values())

    purchases = [
        row for row in summary_rows
        if (row.get("cash_direction") or row.get("direction")) == "purchase"
        and row.get("direction") != "reward"
    ]
    sales = [
        row for row in summary_rows
        if (row.get("cash_direction") or row.get("direction")) == "sale"
    ]
    trades = [row for row in summary_rows if row.get("direction") == "trade"]
    rewards = [row for row in summary_rows if row.get("direction") == "reward"]
    trade_cash_in = sum(
        Decimal(str(row.get("net_eur") or 0)) for row in trades if row.get("cash_direction") == "sale"
    )
    trade_cash_out = sum(
        Decimal(str(row.get("net_eur") or 0)) for row in trades if row.get("cash_direction") == "purchase"
    )

    purchases_inseason = [row for row in purchases if _movement_seasonality(row) == "inseason"]
    purchases_classic = [row for row in purchases if _movement_seasonality(row) == "classic"]
    purchases_mixed = [row for row in purchases if _movement_seasonality(row) == "mixed"]
    sales_inseason = [row for row in sales if _movement_seasonality(row) == "inseason"]
    sales_classic = [row for row in sales if _movement_seasonality(row) == "classic"]
    sales_mixed = [row for row in sales if _movement_seasonality(row) == "mixed"]
    essence_totals = {"limited": 0, "rare": 0, "super_rare": 0}
    for reward in rewards:
        essence_items = reward.get("essence") or []
        if essence_items:
            for item in essence_items:
                item_rarity = str(item.get("rarity") or "")
                if item_rarity in essence_totals:
                    essence_totals[item_rarity] += int(item.get("quantity") or 0)
        elif reward.get("essence_quantity"):
            reward_rarity = str(reward.get("reward_rarity") or "")
            if reward_rarity in essence_totals:
                essence_totals[reward_rarity] += int(reward.get("essence_quantity") or 0)
    totals = {
        "purchases": sum(Decimal(str(row.get("gross_eur") or 0)) for row in purchases),
        "sales_gross": sum(Decimal(str(row.get("gross_eur") or 0)) for row in sales),
        "sales_net": sum(Decimal(str(row.get("net_eur") or 0)) for row in sales),
        "fees": sum(Decimal(str(row.get("fee_eur") or 0)) for row in summary_rows),
        "purchase_count": len(purchases),
        "purchases_inseason": sum(Decimal(str(row.get("gross_eur") or 0)) for row in purchases_inseason),
        "purchases_classic": sum(Decimal(str(row.get("gross_eur") or 0)) for row in purchases_classic),
        "purchases_mixed": sum(Decimal(str(row.get("gross_eur") or 0)) for row in purchases_mixed),
        "sales_inseason": sum(Decimal(str(row.get("net_eur") or 0)) for row in sales_inseason),
        "sales_classic": sum(Decimal(str(row.get("net_eur") or 0)) for row in sales_classic),
        "sales_mixed": sum(Decimal(str(row.get("net_eur") or 0)) for row in sales_mixed),
        "sale_count": len(sales),
        "trade_count": len(trades),
        "movement_count": len(summary_rows),
        "trade_cash_in": trade_cash_in,
        "trade_cash_out": trade_cash_out,
        "rewards": len(rewards),
        "reward_money": sum(Decimal(str(row.get("gross_eur") or 0)) for row in rewards if row.get("reward_type") == "money"),
        "reward_cards": sum(len(row.get("cards") or []) for row in rewards if row.get("reward_type") == "card"),
        "essence_limited": essence_totals["limited"],
        "essence_rare": essence_totals["rare"],
        "essence_super_rare": essence_totals["super_rare"],
        "balance": (
            sum(Decimal(str(row.get("net_eur") or 0)) for row in sales)
            - sum(Decimal(str(row.get("gross_eur") or 0)) for row in purchases)
        ),
    }
    try:
        per_page = int(request.GET.get("per_page", 25))
    except ValueError:
        per_page = 25
    per_page = per_page if per_page in {25, 50, 100} else 25
    page_obj = Paginator(display_rows, per_page).get_page(request.GET.get("page", 1))
    query = request.GET.copy()
    query.pop("page", None)
    query.pop("grouped", None)

    return render(request, "dashboard/movements.html", {
        "snapshot": snapshot,
        "active_sync": active_sync,
        "page_obj": page_obj,
        "total_rows": len(display_rows),
        "all_count": sum(row.get("category") != "reward" for row in all_movements),
        "laliga_count": sum(row.get("category") == "laliga_inseason" for row in all_movements),
        "reward_count": sum(row.get("category") == "reward" for row in all_movements),
        "other_count": sum(row.get("category") == "other" for row in all_movements),
        "totals": totals,
        "available_rarities": sorted(rarities),
        "selected_category": category,
        "selected_direction": direction,
        "selected_rarity": rarity,
        "selected_reward_type": reward_type,
        "selected_seasonality": seasonality,
        "selected_manager": selected_manager,
        "manager_nickname": manager_nickname,
        "public_rewards": public_rewards,
        "date_from": date_from,
        "date_to": date_to,
        "per_page": per_page,
        "query_without_page": query.urlencode(),
    })


def movement_analytics(request):
    snapshot = MovementSnapshot.objects.filter(user=request.user, source_version__gte=14).first()
    if not snapshot:
        return render(request, "dashboard/movement_analytics.html", {"snapshot": None})

    today = date.today()
    preset = request.GET.get("preset", "season")
    preset_dates = {
        "season": date(2026, 8, 12),
        "7d": today - timedelta(days=6),
        "30d": today - timedelta(days=29),
        "month": today.replace(day=1),
    }
    requested_from = request.GET.get("date_from")
    if requested_from is not None:
        date_from = requested_from.strip()
        preset = "custom"
    else:
        date_from = preset_dates.get(preset, preset_dates["season"]).isoformat()
        if preset not in preset_dates:
            preset = "season"
    date_to = request.GET.get("date_to", "").strip()
    seasonality = request.GET.get("seasonality", "")
    if seasonality not in {"", "inseason", "classic"}:
        seasonality = ""
    rarity = request.GET.get("rarity", "")
    if rarity not in {"", "limited", "rare", "super_rare", "unique"}:
        rarity = ""
    category = request.GET.get("category", "all")
    if category not in {"all", "laliga_inseason", "other"}:
        category = "all"
    market = request.GET.get("market", "").strip()
    grouping = request.GET.get("grouping", "day")
    if grouping not in {"day", "week", "month"}:
        grouping = "day"

    base_rows = []
    prepared = []
    markets = set()
    for raw in snapshot.movements or []:
        if raw.get("direction") == "reward" or raw.get("category") == "reward":
            continue
        row = dict(raw)
        occurred_at = _movement_datetime(row.get("occurred_at"))
        if not occurred_at:
            continue
        row["occurred_at_dt"] = occurred_at
        local_date = occurred_at.astimezone(ZoneInfo("Europe/Madrid")).date()
        row["local_date"] = local_date
        if row.get("market"):
            markets.add(str(row["market"]))
        if date_from and local_date.isoformat() < date_from:
            continue
        if date_to and local_date.isoformat() > date_to:
            continue
        if category != "all" and row.get("category") != category:
            continue
        main_cards = _movement_main_cards(row)
        if seasonality and _movement_seasonality(row) != seasonality:
            continue
        if rarity and not any(card.get("rarity") == rarity for card in main_cards):
            continue
        base_rows.append(row)
        if not market or row.get("market") == market:
            prepared.append(row)

    purchases = [
        row for row in prepared
        if (row.get("cash_direction") or row.get("direction")) == "purchase"
    ]
    sales = [
        row for row in prepared
        if (row.get("cash_direction") or row.get("direction")) == "sale"
    ]
    purchase_total = sum(Decimal(str(row.get("gross_eur") or 0)) for row in purchases)
    sales_total = sum(Decimal(str(row.get("net_eur") or 0)) for row in sales)
    fees_total = sum(Decimal(str(row.get("fee_eur") or 0)) for row in prepared)

    cycles = build_trade_cycles(base_rows)
    realized_cycles = [
        cycle for cycle in cycles
        if cycle.get("is_complete") and cycle.get("balance_eur") is not None
        and (
            not market
            or (cycle.get("purchase") or {}).get("market") == market
            or (cycle.get("sale") or {}).get("market") == market
        )
    ]
    realized_profit = sum(Decimal(str(cycle["balance_eur"])) for cycle in realized_cycles)
    realized_cost = sum(Decimal(str(cycle.get("purchase_cost_eur") or 0)) for cycle in realized_cycles)
    realized_roi = (
        realized_profit * Decimal("100") / realized_cost
        if realized_cost else None
    )

    # Capital con coste conocido: compras individuales del intervalo que aún
    # no han tenido una salida exacta. Las cartas recibidas como pago no se
    # valoran artificialmente.
    held = {}
    for row in sorted(base_rows, key=lambda item: item["occurred_at_dt"]):
        received = row.get("received_cards") or (
            row.get("cards") or [] if row.get("direction") == "purchase" else []
        )
        sent = row.get("sent_cards") or (
            row.get("cards") or [] if row.get("direction") == "sale" else []
        )
        cash_direction = row.get("cash_direction") or row.get("direction")
        if (
            cash_direction == "purchase" and len(received) == 1 and not sent
            and (not market or row.get("market") == market)
        ):
            card = received[0]
            asset_id = str(card.get("asset_id") or "")
            if asset_id:
                held[asset_id] = {
                    "card": card,
                    "cost": Decimal(str(row.get("gross_eur") or 0)),
                }
        for card in sent:
            asset_id = str(card.get("asset_id") or "")
            if asset_id:
                held.pop(asset_id, None)
    invested_total = sum(item["cost"] for item in held.values())

    breakdown = []
    for value, label in (("inseason", "In-Season"), ("classic", "Classic")):
        typed_purchases = [row for row in purchases if _movement_seasonality(row) == value]
        typed_sales = [row for row in sales if _movement_seasonality(row) == value]
        typed_cycles = [
            cycle for cycle in realized_cycles
            if bool((cycle.get("purchase_card") or cycle.get("sale_card") or {}).get("in_season"))
            == (value == "inseason")
        ]
        typed_held = [
            item for item in held.values()
            if bool(item["card"].get("in_season")) == (value == "inseason")
        ]
        spent = sum(Decimal(str(row.get("gross_eur") or 0)) for row in typed_purchases)
        received = sum(Decimal(str(row.get("net_eur") or 0)) for row in typed_sales)
        breakdown.append({
            "key": value,
            "label": label,
            "spent": spent,
            "received": received,
            "balance": received - spent,
            "realized": sum(Decimal(str(cycle["balance_eur"])) for cycle in typed_cycles),
            "invested": sum(item["cost"] for item in typed_held),
            "held_count": len(typed_held),
        })

    timeline = defaultdict(lambda: {"purchases": Decimal("0"), "sales": Decimal("0")})
    for row in purchases + sales:
        row_date = row["local_date"]
        if grouping == "month":
            key = row_date.replace(day=1)
            label = key.strftime("%m/%Y")
        elif grouping == "week":
            key = row_date - timedelta(days=row_date.weekday())
            label = f"Semana {key.strftime('%d/%m')}"
        else:
            key = row_date
            label = key.strftime("%d/%m")
        bucket = timeline[(key, label)]
        cash_direction = row.get("cash_direction") or row.get("direction")
        if cash_direction == "purchase":
            bucket["purchases"] += Decimal(str(row.get("gross_eur") or 0))
        elif cash_direction == "sale":
            bucket["sales"] += Decimal(str(row.get("net_eur") or 0))
    timeline_rows = []
    scale = max(
        [abs(values["sales"] - values["purchases"]) for values in timeline.values()] or [Decimal("1")]
    ) or Decimal("1")
    for (key, label), values in sorted(timeline.items()):
        balance = values["sales"] - values["purchases"]
        timeline_rows.append({
            "label": label,
            "purchases": values["purchases"],
            "sales": values["sales"],
            "balance": balance,
            "balance_width": float(abs(balance) * 50 / scale),
        })

    totals = {
        "purchases": purchase_total,
        "purchase_count": len(purchases),
        "sales": sales_total,
        "sale_count": len(sales),
        "balance": sales_total - purchase_total,
        "fees": fees_total,
        "realized_profit": realized_profit,
        "realized_count": len(realized_cycles),
        "realized_roi": realized_roi,
        "invested": invested_total,
        "held_count": len(held),
    }
    return render(request, "dashboard/movement_analytics.html", {
        "snapshot": snapshot,
        "totals": totals,
        "breakdown": breakdown,
        "timeline": timeline_rows,
        "markets": sorted(markets),
        "selected_preset": preset,
        "selected_category": category,
        "selected_seasonality": seasonality,
        "selected_rarity": rarity,
        "selected_market": market,
        "selected_grouping": grouping,
        "date_from": date_from,
        "date_to": date_to,
    })


@require_POST
def enqueue_movements_sync(request):
    selected_manager = request.GET.get("manager", "me")
    if selected_manager == PUBLIC_REWARD_MANAGER_SLUG:
        active = PublicRewardSyncJob.objects.filter(
            manager_slug=selected_manager,
            status__in=(PublicRewardSyncJob.Status.QUEUED, PublicRewardSyncJob.Status.RUNNING),
        ).first()
        job = active or PublicRewardSyncJob.objects.create(
            user=request.user,
            manager_slug=selected_manager,
        )
        return JsonResponse({"job_id": job.id, "status": job.status}, status=202)
    active = MovementSyncJob.objects.filter(
        user=request.user,
        status__in=(MovementSyncJob.Status.QUEUED, MovementSyncJob.Status.RUNNING),
    ).first()
    job = active or MovementSyncJob.objects.create(user=request.user)
    return JsonResponse({"job_id": job.id, "status": job.status}, status=202)


@require_GET
def movements_sync_status(request):
    selected_manager = request.GET.get("manager", "me")
    if selected_manager == PUBLIC_REWARD_MANAGER_SLUG:
        job = PublicRewardSyncJob.objects.filter(
            manager_slug=selected_manager,
        ).order_by("-created_at").first()
    else:
        job = MovementSyncJob.objects.filter(user=request.user).order_by("-created_at").first()
    if not job:
        return JsonResponse({"job": None})
    return JsonResponse({"job": {
        "id": job.id,
        "status": job.status,
        "movement_count": job.movement_count,
        "processed_count": job.processed_count,
        "progress_label": job.progress_label,
        "error": job.error,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }})


def telegram_alerts(request):
    initial_source = load_telegram_alert_payload(PATHS)
    initial = {
        "notify_mode": initial_source["NOTIFY_MODE"],
        "notify_drop_eur": initial_source["NOTIFY_DROP_EUR"],
        "send_all_offers_below_threshold": initial_source["SEND_ALL_OFFERS_BELOW_THRESHOLD"].lower() == "true",
        "send_run_start_message": initial_source["SEND_RUN_START_MESSAGE"].lower() == "true",
        "send_single_message": initial_source["SEND_SINGLE_MESSAGE"].lower() == "true",
        "include_player_preview": initial_source["INCLUDE_PLAYER_PREVIEW"].lower() == "true",
        "rarity": initial_source["RARITY"],
        "season_year": initial_source["SEASON_YEAR"],
        "in_season_year": initial_source["IN_SEASON_YEAR"],
        "classic_players": initial_source["classic_players"],
        "in_season_players": initial_source["in_season_players"],
    }

    script_result = None
    if request.method == "POST":
        form = TelegramSettingsForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            save_telegram_alert_payload(
                PATHS,
                {
                    "NOTIFY_MODE": data["notify_mode"],
                    "NOTIFY_DROP_EUR": str(data["notify_drop_eur"]),
                    "SEND_ALL_OFFERS_BELOW_THRESHOLD": _to_bool_text(bool(data["send_all_offers_below_threshold"])),
                    "SEND_RUN_START_MESSAGE": _to_bool_text(bool(data["send_run_start_message"])),
                    "SEND_SINGLE_MESSAGE": _to_bool_text(bool(data["send_single_message"])),
                    "INCLUDE_PLAYER_PREVIEW": _to_bool_text(bool(data["include_player_preview"])),
                    "RARITY": data["rarity"],
                    "SEASON_YEAR": str(data["season_year"] or ""),
                    "IN_SEASON_YEAR": str(data["in_season_year"] or ""),
                    "classic_players": data["classic_players"],
                    "in_season_players": data["in_season_players"],
                },
            )

            action = request.POST.get("action")
            if action == "save_and_run":
                script_result = run_telegram_alert(PATHS)
                if script_result.exit_code == 0:
                    messages.success(request, "Configuracion guardada y alerta ejecutada.")
                else:
                    messages.error(request, "Configuracion guardada, pero la ejecucion fallo.")
            elif action == "dry_run":
                script_result = run_telegram_alert(PATHS, dry_run=True)
                if script_result.exit_code == 0:
                    messages.success(request, "Dry run ejecutado correctamente.")
                else:
                    messages.error(request, "Dry run con errores.")
            else:
                messages.success(request, "Configuracion de alertas guardada.")

            if action == "save_only":
                return redirect("telegram_alerts")
        else:
            messages.error(request, "Revisa el formulario, hay campos invalidos.")
    else:
        form = TelegramSettingsForm(initial=initial)

    return render(
        request,
        "dashboard/telegram.html",
        {
            "form": form,
            "script_result": script_result,
        },
    )


def bid_scheduler(request):
    script_result = None
    if request.method == "POST":
        form = BidScheduleForm(request.POST)
        if form.is_valid():
            payload = form.cleaned_data
            script_result = run_bid_scheduler(
                PATHS,
                BidRequest(
                    identifier=payload["identifier"],
                    euros=str(payload["euros"]),
                    hora=str(payload.get("hora") or ""),
                    now=bool(payload.get("now")),
                    sniper=bool(payload.get("sniper")),
                    background=bool(payload.get("background")),
                    use_credit=bool(payload.get("use_credit")),
                ),
            )
            if script_result.exit_code == 0:
                messages.success(request, "Puja ejecutada/programada correctamente.")
            else:
                messages.error(request, "La puja devolvio error. Revisa el log abajo.")
    else:
        form = BidScheduleForm()

    return render(
        request,
        "dashboard/bid.html",
        {
            "form": form,
            "script_result": script_result,
        },
    )


def _normalize_sales_rarity(rarity: str | None) -> str:
    key = str(rarity or "").strip().lower()
    if key in {"limited", "amarillas"}:
        return "limited"
    if key in {"rare", "rojas"}:
        return "rare"
    if key in {"super_rare", "azules"}:
        return "super_rare"
    return "super_rare"


def _sales_price_filter(value: str | None) -> Decimal | None:
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


def sales_workbench(request):
    selected_rarity = _normalize_sales_rarity(
        request.GET.get("rarity")
        or request.session.get(SALES_SELECTED_RARITY_SESSION_KEY)
    )
    request.session[SALES_SELECTED_RARITY_SESSION_KEY] = selected_rarity
    inventory = SalesInventory.objects.filter(rarity=selected_rarity).first()
    all_cards = list(inventory.cards if inventory else [])
    for card in all_cards:
        card["collection_display_name"] = collection_display_name(card.get("collection_name"))
    available_teams = sorted({card.get("team", "-") for card in all_cards})
    available_positions = sorted({card.get("position", "-") for card in all_cards})
    available_seasons = sorted(
        {card.get("season", "-") for card in all_cards},
        reverse=True,
    )

    player_filter = request.GET.get("player", "").strip().casefold()
    teams = [value for value in request.GET.getlist("teams") if value]
    positions = [value for value in request.GET.getlist("positions") if value]
    season = request.GET.get("season", "").strip()
    in_season = request.GET.get("in_season", "").strip()
    classic_price_from = _sales_price_filter(request.GET.get("classic_price_from"))
    inseason_price_from = _sales_price_filter(request.GET.get("inseason_price_from"))
    show_blocked = request.GET.get("show_blocked") == "1"

    cards = []
    for card in all_cards:
        if card.get("blocked") and not show_blocked:
            continue
        if player_filter and player_filter not in str(card.get("player", "")).casefold():
            continue
        if teams and card.get("team") not in teams:
            continue
        if positions and card.get("position") not in positions:
            continue
        if season and card.get("season") != season:
            continue
        if in_season == "yes" and not card.get("in_season"):
            continue
        if in_season == "no" and card.get("in_season"):
            continue
        classic_price = card.get("min_price_classic")
        if classic_price_from is not None and (
            classic_price is None or Decimal(str(classic_price)) < classic_price_from
        ):
            continue
        inseason_price = card.get("min_price_inseason")
        if inseason_price_from is not None and (
            inseason_price is None or Decimal(str(inseason_price)) < inseason_price_from
        ):
            continue
        cards.append(card)

    try:
        per_page = int(request.GET.get("per_page", 20))
    except ValueError:
        per_page = 20
    per_page = per_page if per_page in {20, 50, 100} else 20
    page_obj = Paginator(cards, per_page).get_page(request.GET.get("page", 1))
    active_refresh = SalesRefreshJob.objects.filter(
        rarity=selected_rarity,
        status__in=(SalesRefreshJob.Status.QUEUED, SalesRefreshJob.Status.RUNNING),
    ).order_by("-created_at").first()

    return render(
        request,
        "dashboard/sales.html",
        {
            "selected_rarity": selected_rarity,
            "selected_rarity_label": {
                "limited": "amarillas", "rare": "rojas", "super_rare": "azules",
            }[selected_rarity],
            "inventory": inventory,
            "total_cards": len(all_cards),
            "blocked_cards": sum(1 for card in all_cards if card.get("blocked")),
            "page_obj": page_obj,
            "available_teams": available_teams,
            "available_positions": available_positions,
            "available_seasons": available_seasons,
            "selected_teams": teams,
            "selected_positions": positions,
            "selected_season": season,
            "selected_in_season": in_season,
            "classic_price_from": request.GET.get("classic_price_from", ""),
            "inseason_price_from": request.GET.get("inseason_price_from", ""),
            "show_blocked": show_blocked,
            "per_page": per_page,
            "active_refresh": active_refresh,
        },
    )


def sales_download_excel(request):
    selected_rarity = _normalize_sales_rarity(request.session.get(SALES_SELECTED_RARITY_SESSION_KEY))
    inventory = SalesInventory.objects.filter(rarity=selected_rarity).first()
    if not inventory:
        return HttpResponse("Primero actualiza las cartas de esta rareza.", status=404)
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Mis cartas"
    sheet.append([
        "Jugador", "Equipo", "Rareza", "Temporada", "Posición", "Liga", "Nivel",
        "In season", "Estado", "Colección", "Rayos colección", "Rayos carta",
        "Rayos tras venta", "Mínimo classic (€)", "Mínimo in-season (€)", "assetId",
    ])
    for card in inventory.cards:
        sheet.append([
            card.get("player"), card.get("team"), card.get("rarity"), card.get("season"),
            card.get("position"), card.get("league"), card.get("grade"),
            "Sí" if card.get("in_season") else "No", card.get("blocked_reason") or "Disponible",
            card.get("collection_name"), card.get("collection_rays"), card.get("card_rays"),
            card.get("rays_after_sale"), card.get("min_price_classic"),
            card.get("min_price_inseason"), card.get("asset_id"),
        ])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return FileResponse(output, as_attachment=True, filename=f"mis_cartas_{selected_rarity}.xlsx")


@require_POST
def enqueue_sales_refresh(request):
    rarity = _normalize_sales_rarity(request.POST.get("rarity"))
    active = SalesRefreshJob.objects.filter(
        rarity=rarity,
        status__in=(SalesRefreshJob.Status.QUEUED, SalesRefreshJob.Status.RUNNING),
    ).first()
    job = active or SalesRefreshJob.objects.create(user=request.user, rarity=rarity)
    return JsonResponse({"job_id": job.id, "status": job.status, "rarity": rarity}, status=202)


@require_POST
def enqueue_batch_sales(request):
    form = BatchSaleForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": form.errors.get_json_data()}, status=400)
    try:
        request_key = uuid.UUID(request.POST.get("request_key", ""))
    except ValueError:
        return JsonResponse({"error": "La solicitud de venta no es válida."}, status=400)
    existing = SaleBatchJob.objects.filter(user=request.user, request_key=request_key).first()
    if existing:
        return JsonResponse({"job_id": existing.id, "status": existing.status, "total": existing.total_count}, status=202)

    requested = form.cleaned_data["sales"]
    inventories = SalesInventory.objects.all()
    cards_by_asset = {
        card.get("asset_id"): card
        for inventory in inventories
        for card in inventory.cards
        if card.get("asset_id")
    }
    items = []
    for position, sale in enumerate(requested, start=1):
        card = cards_by_asset.get(sale["asset_id"])
        if not card:
            return JsonResponse({"error": "Una carta ya no está en el inventario actualizado."}, status=409)
        if card.get("blocked"):
            return JsonResponse({"error": f"{card.get('player')}: {card.get('blocked_reason') or 'no se puede vender'}."}, status=409)
        items.append((position, sale, card))

    with transaction.atomic():
        job = SaleBatchJob.objects.create(
            user=request.user,
            request_key=request_key,
            total_count=len(items),
        )
        SaleBatchItem.objects.bulk_create([
            SaleBatchItem(
                job=job,
                position=position,
                asset_id=sale["asset_id"],
                player_name=card.get("player") or "Jugador",
                rarity=card.get("rarity") or "",
                euros=sale["euros"],
                minimum_offer_eur=sale["minimum_offer_eur"],
                duration_days=sale["duration_days"],
            )
            for position, sale, card in items
        ])
    return JsonResponse({"job_id": job.id, "status": job.status, "total": job.total_count}, status=202)


@require_GET
def sales_jobs_status(request):
    raw_ids = [value for value in request.GET.get("ids", "").split(",") if value.isdigit()]
    jobs = SaleBatchJob.objects.filter(user=request.user)
    if raw_ids:
        jobs = jobs.filter(id__in=raw_ids)
    else:
        jobs = jobs.order_by("-created_at")[:10]
    refreshes = SalesRefreshJob.objects.order_by("-created_at")[:5]
    return JsonResponse({
        "jobs": [{
            "id": job.id,
            "status": job.status,
            "total": job.total_count,
            "success": job.success_count,
            "failure": job.failure_count,
            "items": [{
                "player": item.player_name,
                "status": item.status,
                "error": item.error,
            } for item in job.items.all()],
        } for job in jobs],
        "refreshes": [{
            "id": job.id,
            "rarity": job.rarity,
            "status": job.status,
            "card_count": job.card_count,
            "processed_count": job.processed_count,
            "total_count": job.total_count,
            "percent": round(job.processed_count * 100 / job.total_count) if job.total_count else 0,
            "progress_label": job.progress_label,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "error": job.error,
        } for job in refreshes],
    })


def auctions_list(request):
    """Subastas Rare de LaLiga correspondientes a la temporada 2026-2027."""
    import listar_subastas
    
    auctions = []
    error = None
    loading = True

    if request.method == "POST" and request.POST.get("action") == "place_bid":
        bid_form = InlineBidForm(request.POST)
        if bid_form.is_valid():
            data = bid_form.cleaned_data
            result = run_bid_scheduler(
                PATHS,
                BidRequest(
                    identifier=data["auction_id"],
                    euros=str(data["euros"]),
                    hora="",
                    now=True,
                    sniper=False,
                    background=False,
                    use_credit=bool(data["use_credit"]),
                    currency=data["currency"],
                ),
            )
            if result.exit_code == 0:
                messages.success(request, f"Puja de {data['euros']:.2f} € enviada correctamente.")
            else:
                detail = result.stderr or result.stdout or "Error desconocido"
                messages.error(request, f"La puja no se pudo completar: {detail[-500:]}")
            return redirect(request.get_full_path())
        messages.error(request, "No se envió la puja: revisa el importe y la confirmación.")

    try:
        auctions = listar_subastas.fetch_la_liga_rare_auctions(
            team_filters=None,
            rarity="rare",
            season_year=2026,
        )
        loading = False
    except Exception as e:
        error = str(e)
        messages.error(request, f"Error al cargar subastas: {error}")
    
    cache_metadata = listar_subastas.load_auction_cache() or {}
    balance_cache = cache.get("sorare_account_balances")
    if balance_cache is None:
        try:
            from sorare_utils import build_headers, get_account_balances
            balance_cache = {"balances": get_account_balances(headers=build_headers()), "error": None}
        except (Exception, SystemExit):
            balance_cache = {"balances": None, "error": "No se pudo consultar ahora"}
        cache.set("sorare_account_balances", balance_cache, 60)
    account_balances = balance_cache["balances"]
    balance_error = balance_cache["error"]
    madrid = ZoneInfo('Europe/Madrid')

    def market_timestamp(value):
        if not value:
            return None
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(madrid)
        today = datetime.now(madrid).date()
        days_ago = (today - timestamp.date()).days
        if days_ago == 0:
            return f"Hoy {timestamp:%H:%M}"
        if days_ago == 1:
            return f"Ayer {timestamp:%H:%M}"
        return timestamp.strftime('%d/%m/%Y %H:%M')

    available_teams = sorted({auction['team'] for auction in auctions})
    available_positions = sorted({auction['position'] for auction in auctions})
    filter_player = request.GET.get('player', '').strip()
    filter_teams = [team.strip() for team in request.GET.getlist('teams') if team.strip()]
    filter_positions = [position.strip() for position in request.GET.getlist('positions') if position.strip()]
    filter_status = request.GET.get('status', '').strip()
    has_bid_only = request.GET.get('has_bid') == '1'
    favorites_only = request.GET.get('favorites') == '1'
    favorite_slugs = set()
    if getattr(request, "user", None) and request.user.is_authenticated:
        favorite_slugs = set(
            FavoritePlayer.objects.filter(user=request.user).values_list('player_slug', flat=True)
        )
    end_order = request.GET.get('end_order', 'asc')
    if end_order not in {'asc', 'desc'}:
        end_order = 'asc'

    auctions.sort(
        key=lambda row: datetime.fromisoformat(row['end_date'].replace('Z', '+00:00')),
        reverse=end_order == 'desc',
    )

    filtered_auctions = auctions
    if filter_player:
        filtered_auctions = [row for row in filtered_auctions if filter_player.casefold() in row['player'].casefold()]
    if filter_teams:
        filtered_auctions = [row for row in filtered_auctions if row['team'] in set(filter_teams)]
    if filter_positions:
        filtered_auctions = [row for row in filtered_auctions if row['position'] in set(filter_positions)]
    if filter_status == 'winning':
        filtered_auctions = [row for row in filtered_auctions if row['is_winning']]
    elif filter_status == 'outbid':
        filtered_auctions = [row for row in filtered_auctions if row['is_outbid']]
    if has_bid_only:
        filtered_auctions = [row for row in filtered_auctions if row['has_bid']]
    if favorites_only:
        filtered_auctions = [row for row in filtered_auctions if row['player_slug'] in favorite_slugs]

    for auction in filtered_auctions:
        auction['is_favorite'] = auction['player_slug'] in favorite_slugs

    today_madrid = datetime.now(madrid).date()
    for auction in filtered_auctions:
        end_at = datetime.fromisoformat(auction['end_date'].replace('Z', '+00:00')).astimezone(madrid)
        days_until = (end_at.date() - today_madrid).days
        if days_until == 0:
            auction['end_date_madrid'] = f"Hoy {end_at:%H:%M}"
        elif days_until == 1:
            auction['end_date_madrid'] = f"Mañana {end_at:%H:%M}"
        else:
            auction['end_date_madrid'] = end_at.strftime('%d/%m/%Y %H:%M')

    try:
        per_page = int(request.GET.get('per_page', 20))
    except (TypeError, ValueError):
        per_page = 20
    if per_page not in {20, 50, 100}:
        per_page = 20
    paginator = Paginator(filtered_auctions, per_page)
    page_obj = paginator.get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)
    sort_query_params = query_params.copy()
    sort_query_params.pop('end_order', None)
    saved_filters = []
    if getattr(request, "user", None) and request.user.is_authenticated:
        saved_filters = AuctionFilterPreset.objects.filter(user=request.user)

    return render(
        request,
        "dashboard/auctions.html",
        {
            "auctions": page_obj.object_list,
            "page_obj": page_obj,
            "filtered_count": len(filtered_auctions),
            "available_teams": available_teams,
            "available_positions": available_positions,
            "filter_player": filter_player,
            "filter_teams": filter_teams,
            "filter_positions": filter_positions,
            "filter_status": filter_status,
            "has_bid_only": has_bid_only,
            "favorites_only": favorites_only,
            "favorite_count": len(favorite_slugs),
            "end_order": end_order,
            "pagination_query": query_params.urlencode(),
            "sort_query": sort_query_params.urlencode(),
            "error": error,
            "loading": loading,
            "season_label": "2026-2027",
            "market_updated_at": market_timestamp(cache_metadata.get('updated_at')),
            "full_market_updated_at": market_timestamp(cache_metadata.get('full_refreshed_at')),
            "quick_market_updated_at": market_timestamp(cache_metadata.get('quick_refreshed_at')),
            "last_new_cards_at": market_timestamp(cache_metadata.get('last_new_cards_at')),
            "last_new_cards_count": cache_metadata.get('new_cards_count', 0),
            "account_balances": account_balances,
            "balance_error": balance_error,
            "per_page": per_page,
            "saved_filters": saved_filters,
        },
    )


@require_POST
def enqueue_auction_refresh(request):
    """Encola un refresco rápido o un barrido completo sin bloquear la página."""
    import listar_subastas

    mode = request.POST.get("mode", "quick").strip()
    if mode not in {AuctionRefreshJob.Mode.QUICK, AuctionRefreshJob.Mode.FULL}:
        return JsonResponse({"error": "Tipo de actualización no válido."}, status=400)
    if mode == AuctionRefreshJob.Mode.QUICK and not listar_subastas.load_auction_cache():
        return JsonResponse({"error": "Primero hay que realizar una búsqueda completa del mercado."}, status=409)

    active = AuctionRefreshJob.objects.filter(
        user=request.user,
        status__in=(AuctionRefreshJob.Status.QUEUED, AuctionRefreshJob.Status.RUNNING),
    ).order_by("created_at").first()
    if active and (active.mode == AuctionRefreshJob.Mode.FULL or active.mode == mode):
        job = active
    else:
        job = AuctionRefreshJob.objects.create(user=request.user, mode=mode)
    return JsonResponse({"job_id": job.id, "mode": job.mode, "status": job.status}, status=202)


@require_GET
def auction_refresh_status(request):
    try:
        job_id = int(request.GET.get("id", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Identificador no válido."}, status=400)
    job = AuctionRefreshJob.objects.filter(pk=job_id, user=request.user).first()
    if not job:
        return JsonResponse({"error": "Actualización no encontrada."}, status=404)
    percent = round(job.processed_count * 100 / job.total_count) if job.total_count else 0
    return JsonResponse({
        "job": {
            "id": job.id,
            "mode": job.mode,
            "status": job.status,
            "processed_count": job.processed_count,
            "total_count": job.total_count,
            "percent": min(percent, 100),
            "auction_count": job.auction_count,
            "new_cards_count": job.new_cards_count,
            "progress_label": job.progress_label,
            "error": job.error,
        },
    })


@require_POST
def save_auction_filter(request):
    from django.http import QueryDict

    name = request.POST.get("name", "").strip()
    if not 1 <= len(name) <= 60:
        messages.error(request, "Pon un nombre de entre 1 y 60 caracteres.")
        return redirect("auctions_list")
    supplied = QueryDict(request.POST.get("query", "").lstrip("?"))
    allowed = {"player", "teams", "positions", "status", "has_bid", "favorites", "per_page", "end_order"}
    clean = QueryDict("", mutable=True)
    for key in allowed:
        for value in supplied.getlist(key):
            if len(value) <= 180:
                clean.appendlist(key, value)
    AuctionFilterPreset.objects.update_or_create(user=request.user, name=name, defaults={"query_string": clean.urlencode()})
    messages.success(request, f'Filtro "{name}" guardado.')
    target = reverse("auctions_list")
    return redirect(f"{target}?{clean.urlencode()}" if clean else target)


@require_POST
def delete_auction_filter(request):
    preset = AuctionFilterPreset.objects.filter(user=request.user, id=request.POST.get("preset_id")).first()
    if preset:
        preset.delete()
        messages.success(request, "Filtro guardado eliminado.")
    next_path = request.POST.get("next", "")
    auctions_path = reverse("auctions_list")
    return redirect(next_path if next_path.startswith(auctions_path) else auctions_path)


@require_POST
def enqueue_batch_bids(request):
    """Guarda un lote para que el worker lo procese sin bloquear el navegador."""
    import listar_subastas

    form = BatchBidForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Revisa los identificadores y los importes antes de confirmar."}, status=400)
    try:
        request_key = uuid.UUID(request.POST.get("request_key", ""))
    except (TypeError, ValueError, AttributeError):
        return JsonResponse({"error": "La solicitud de puja no es válida."}, status=400)
    existing = BidBatchJob.objects.filter(user=request.user, request_key=request_key).first()
    if existing:
        return JsonResponse({"job_id": existing.id, "status": existing.status, "total": existing.total_count}, status=202)
    cached_market = listar_subastas.load_auction_cache() or {}
    player_by_auction = {row.get("auction_id"): row.get("player", "Jugador desconocido") for row in cached_market.get("auctions", [])}
    bids = form.cleaned_data["bids"]
    with transaction.atomic():
        job = BidBatchJob.objects.create(user=request.user, request_key=request_key, total_count=len(bids))
        BidBatchItem.objects.bulk_create([
            BidBatchItem(job=job, position=index, auction_id=data["auction_id"],
                         player_name=player_by_auction.get(data["auction_id"], f"Puja {index}"),
                         euros=data["euros"], use_credit=bool(data["use_credit"]), currency=data["currency"])
            for index, data in enumerate(bids, start=1)
        ])
    return JsonResponse({"job_id": job.id, "status": job.status, "total": job.total_count}, status=202)


@require_GET
def bid_jobs_status(request):
    try:
        job_ids = list(dict.fromkeys(int(value) for value in request.GET.get("ids", "").split(",") if value.strip()))[:30]
    except ValueError:
        return JsonResponse({"error": "Identificadores no válidos."}, status=400)
    jobs = BidBatchJob.objects.filter(user=request.user, id__in=job_ids).prefetch_related("items")
    return JsonResponse({"jobs": [{
        "id": job.id, "status": job.status, "total": job.total_count,
        "successes": job.success_count, "failures": job.failure_count,
        "items": [{"player": item.player_name, "status": item.status, "error": item.error} for item in job.items.all()],
    } for job in jobs]})


def toggle_favorite_player(request):
    """Añade o elimina un jugador de los favoritos del usuario autenticado."""
    import re

    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido"}, status=405)
    player_slug = request.POST.get("player_slug", "").strip()
    player_name = request.POST.get("player_name", "").strip()
    if not re.fullmatch(r"[a-z0-9-]{1,180}", player_slug) or not 1 <= len(player_name) <= 180:
        return JsonResponse({"error": "Jugador no válido"}, status=400)

    favorite, created = FavoritePlayer.objects.get_or_create(
        user=request.user,
        player_slug=player_slug,
        defaults={"player_name": player_name},
    )
    if not created:
        favorite.delete()
    return JsonResponse({"favorite": created})


def _classify_token_price_deal(deal):
    """Devuelve la presentación del origen de una venta registrada por Sorare."""
    deal = deal or {}
    typename = deal.get("__typename")
    offer_type = deal.get("type")
    sender_cards = (deal.get("senderSide") or {}).get("anyCards") or []
    receiver_cards = (deal.get("receiverSide") or {}).get("anyCards") or []

    if typename == "TokenAuction":
        return "auction", "fa-gavel", "Subasta"
    if typename == "TokenPrimaryOffer":
        return "instant", "fa-bolt", "Compra instantánea"
    if typename == "TokenOffer" and sender_cards and receiver_cards:
        return "trade", "fa-right-left", "Intercambio entre managers"
    if typename == "TokenOffer" and offer_type == "SINGLE_BUY_OFFER":
        return "public", "fa-bullhorn", "Oferta pública"
    if typename == "TokenOffer" and offer_type == "SINGLE_SALE_OFFER":
        return "instant", "fa-bolt", "Compra instantánea"
    if typename == "TokenOffer" and offer_type == "DIRECT_OFFER":
        return "direct", "fa-handshake", "Oferta directa"
    if typename == "TokenOffer":
        return "direct", "fa-handshake", "Oferta entre managers"
    return "trade", "fa-right-left", "Operación entre managers"


def auction_price_history(request):
    """Últimas cinco ventas Rare 2026 del jugador solicitado."""
    import re
    from sorare_utils import build_headers, get_recent_prices

    player_slug = request.GET.get("player_slug", "").strip()
    if not re.fullmatch(r"[a-z0-9-]{1,160}", player_slug):
        return JsonResponse({"error": "Jugador no válido"}, status=400)

    try:
        prices = get_recent_prices(
            player_slug,
            rarity="rare",
            season=2026,
            first=5,
            headers=build_headers(),
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    sales = []
    for price in prices[:5]:
        deal = price.get("deal") or {}
        kind, icon, label = _classify_token_price_deal(deal)
        eur_cents = (price.get("amounts") or {}).get("eurCents")
        sales.append(
            {
                "date": price.get("date"),
                "eur": eur_cents / 100 if eur_cents is not None else None,
                "kind": kind,
                "icon": icon,
                "label": label,
            }
        )
    return JsonResponse({"sales": sales, "season": "2026-2027", "rarity": "Rare"})


@require_GET
def sales_price_history(request):
    """Últimas ventas comparables para una carta del inventario de Ventas."""
    import re
    from sorare_utils import build_headers, get_recent_prices

    player_slug = request.GET.get("player_slug", "").strip()
    rarity = request.GET.get("rarity", "").strip().lower()
    mode = request.GET.get("mode", "").strip().lower()
    try:
        season_year = int(request.GET.get("season_year", ""))
    except ValueError:
        season_year = 0
    if not re.fullmatch(r"[a-z0-9-]{1,160}", player_slug):
        return JsonResponse({"error": "Jugador no válido"}, status=400)
    if rarity not in {"limited", "rare", "super_rare"} or mode not in {"in_season", "classic"}:
        return JsonResponse({"error": "Comparación no válida"}, status=400)
    if mode == "in_season" and not 2000 <= season_year <= 2100:
        return JsonResponse({"error": "Temporada no válida"}, status=400)

    try:
        prices = get_recent_prices(
            player_slug,
            rarity=rarity,
            season=season_year if mode == "in_season" else None,
            first=5,
            season_eligibility="IN_SEASON" if mode == "in_season" else "CLASSIC",
            headers=build_headers(),
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    comparable = []
    for price in prices:
        sold_card = price.get("card") or {}
        sold_in_season = bool(sold_card.get("inSeasonEligible"))
        if (mode == "in_season") != sold_in_season:
            continue
        deal = price.get("deal") or {}
        kind, icon, label = _classify_token_price_deal(deal)
        eur_cents = (price.get("amounts") or {}).get("eurCents")
        comparable.append({
            "date": price.get("date"),
            "eur": eur_cents / 100 if eur_cents is not None else None,
            "kind": kind,
            "icon": icon,
            "label": label,
            "season_year": sold_card.get("seasonYear"),
        })
        if len(comparable) == 5:
            break

    rarity_label = {"limited": "Limited", "rare": "Rare", "super_rare": "Super Rare"}[rarity]
    return JsonResponse({
        "sales": comparable,
        "rarity": rarity_label,
        "mode": "In season" if mode == "in_season" else "Classic",
    })


@require_POST
def auction_bid_comparisons(request):
    """Última venta comparable para los jugadores incluidos en un resumen."""
    import json
    import re
    from sorare_utils import build_headers, get_latest_prices

    try:
        body = json.loads(request.body)
        raw_slugs = body.get("player_slugs", [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "La solicitud de comparación no es válida."}, status=400)
    if not isinstance(raw_slugs, list):
        return JsonResponse({"error": "La lista de jugadores no es válida."}, status=400)

    player_slugs = list(dict.fromkeys(str(slug).strip() for slug in raw_slugs))
    if not 1 <= len(player_slugs) <= 20 or any(
        not re.fullmatch(r"[a-z0-9-]{1,160}", slug) for slug in player_slugs
    ):
        return JsonResponse({"error": "Selecciona entre 1 y 20 jugadores válidos."}, status=400)

    cache_keys = {slug: f"auction-last-price:rare:2026:{slug}" for slug in player_slugs}
    cached = cache.get_many(cache_keys.values())
    missing = [slug for slug in player_slugs if cache_keys[slug] not in cached]
    if missing:
        try:
            latest = get_latest_prices(
                missing,
                rarity="rare",
                season=2026,
                headers=build_headers(),
            )
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        new_cache_values = {
            cache_keys[slug]: (latest.get(slug) or {})
            for slug in missing
        }
        cache.set_many(new_cache_values, timeout=300)
        cached.update(new_cache_values)

    comparisons = {}
    for slug in player_slugs:
        price = cached.get(cache_keys[slug]) or {}
        eur_cents = (price.get("amounts") or {}).get("eurCents")
        kind, _icon, label = _classify_token_price_deal(price.get("deal"))
        comparisons[slug] = {
            "eur": eur_cents / 100 if eur_cents is not None else None,
            "date": price.get("date"),
            "kind": kind if price else None,
            "label": label if price else None,
        }
    return JsonResponse({"comparisons": comparisons})


def offers_received(request):
    """Muestra las ofertas recibidas pendientes."""
    import listar_ofertas_recibidas

    offers = []
    eth_rate = 0
    error = None
    token_expired = False
    try:
        offers, eth_rate = listar_ofertas_recibidas.fetch_pending_offers_received()
    except Exception as e:
        error = str(e)
        lowered = error.lower()
        if "signature has expired" in lowered or "unauthorized" in lowered:
            token_expired = True

    return render(
        request,
        "dashboard/offers_received.html",
        {
            "offers": offers,
            "error": error,
            "total": len(offers),
            "eth_rate": eth_rate,
            "token_expired": token_expired,
        },
    )


@csrf_exempt
def offers_market_prices(request):
    """AJAX endpoint: calcula precios mínimos de mercado para una lista de asset_ids."""
    import json
    from sorare_utils import build_headers, fetch_exchange_rates, get_all_min_prices_for_player

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
        asset_ids = body.get("asset_ids", [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    headers = build_headers()
    rates = fetch_exchange_rates()
    results = {}
    for asset_id in asset_ids:
        if not asset_id or asset_id in results:
            continue
        prices = get_all_min_prices_for_player(asset_id, headers=headers, rates=rates)
        results[asset_id] = prices

    return JsonResponse({"prices": results})


def refresh_token(request):
    """Renueva el JWT de Sorare desde la web (login + MFA en dos pasos)."""
    awaiting_otp = bool(request.session.get(OTP_CHALLENGE_SESSION_KEY))

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "start":
            result = token_service.begin_refresh()
            if result["status"] == "success":
                request.session.pop(OTP_CHALLENGE_SESSION_KEY, None)
                messages.success(request, "Token renovado correctamente (sin MFA).")
                return redirect("refresh_token")
            if result["status"] == "mfa_required":
                request.session[OTP_CHALLENGE_SESSION_KEY] = result["otp_session_challenge"]
                awaiting_otp = True
                messages.info(request, "Introduce el código MFA de tu autenticador.")
            else:
                messages.error(request, f"Error: {result['message']}")

        elif action == "verify":
            challenge = request.session.get(OTP_CHALLENGE_SESSION_KEY)
            otp_code = (request.POST.get("otp_code") or "").strip()
            if not challenge:
                messages.error(request, "La sesión MFA expiró. Vuelve a empezar.")
            else:
                result = token_service.finish_refresh(challenge, otp_code)
                if result["status"] == "success":
                    request.session.pop(OTP_CHALLENGE_SESSION_KEY, None)
                    messages.success(request, "Token renovado correctamente con MFA.")
                    return redirect("refresh_token")
                messages.error(request, f"Error: {result['message']}")
                awaiting_otp = True

        elif action == "cancel":
            request.session.pop(OTP_CHALLENGE_SESSION_KEY, None)
            awaiting_otp = False
            messages.info(request, "Renovación cancelada.")
            return redirect("refresh_token")

    return render(
        request,
        "dashboard/token.html",
        {
            "status": token_service.token_status(),
            "awaiting_otp": awaiting_otp,
        },
    )
