from __future__ import annotations

import sys
from pathlib import Path
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

from web_services.config_files import SorarePaths, load_telegram_alert_payload, save_telegram_alert_payload
from web_services.process_runner import BidRequest, run_bid_scheduler, run_telegram_alert
from web_services.sales_excel import (
    excel_path_for_rarity,
    execute_sales,
    load_sales_rows,
    reset_prices,
    rows_ready_to_sell,
    run_export_cards,
    save_prices,
)

from .forms import BidScheduleForm, ExportCardsForm, TelegramSettingsForm

REPO_ROOT = Path(__file__).resolve().parents[2]
PATHS = SorarePaths(repo_root=REPO_ROOT)
SALES_SELECTED_RARITY_SESSION_KEY = "sales_selected_rarity"
SALES_CONFIRM_STATE_SESSION_KEY = "sales_confirm_state"
SALES_DURATION_DAYS = 2

# Añadir el directorio src al path para importar listar_subastas
sys.path.insert(0, str(REPO_ROOT / 'src'))


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
            "title": "Ofertas Recibidas",
            "icon": "fas fa-envelope-open-text",
            "description": "Consulta las ofertas pendientes que has recibido por tus cartas.",
            "url": "offers_received",
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


def _current_sales_excel_path(rarity: str) -> Path | None:
    path = excel_path_for_rarity(PATHS, rarity)
    if not path.exists():
        return None
    return path


def _get_confirm_state(request) -> dict | None:
    state = request.session.get(SALES_CONFIRM_STATE_SESSION_KEY)
    if not state:
        return None
    if not isinstance(state, dict):
        return None
    return state


def _clear_confirm_state(request) -> None:
    request.session.pop(SALES_CONFIRM_STATE_SESSION_KEY, None)


def _advance_confirmation_step(request, rows: list[dict], action: str) -> None:
    state = _get_confirm_state(request)
    if not state:
        messages.error(request, "No hay proceso de confirmación activo.")
        return

    queue = [int(v) for v in state.get("queue", [])]
    queue_index = int(state.get("index", 0))
    results = list(state.get("results", []))

    if queue_index >= len(queue):
        messages.info(request, "La confirmación ya estaba terminada.")
        return

    row_id = queue[queue_index]
    by_row = {int(r["row"]): r for r in rows}
    item = by_row.get(row_id)
    if not item:
        results.append({"jugador": f"Fila {row_id}", "status": "skip", "message": "No encontrada en Excel"})
    elif action == "confirm_step_skip":
        results.append({"jugador": item["jugador"], "status": "skip", "message": "Saltada por usuario"})
    else:
        execution = execute_sales(PATHS, rows=rows, selected_rows=[row_id], days=SALES_DURATION_DAYS)
        if execution.items:
            results.extend(execution.items)
        else:
            results.append({"jugador": item["jugador"], "status": "fail", "message": "Sin resultado de ejecución"})

    state["index"] = queue_index + 1
    state["results"] = results
    request.session[SALES_CONFIRM_STATE_SESSION_KEY] = state


def sales_workbench(request):
    if request.method != "POST":
        _clear_confirm_state(request)

    selected_rarity = _normalize_sales_rarity(
        request.POST.get("rarity")
        or request.GET.get("rarity")
        or request.session.get(SALES_SELECTED_RARITY_SESSION_KEY)
    )
    request.session[SALES_SELECTED_RARITY_SESSION_KEY] = selected_rarity

    export_form = ExportCardsForm(initial={"rarity": selected_rarity, "max_cards": 10})
    export_result = None
    execution_result = None

    current_excel = _current_sales_excel_path(selected_rarity)

    if request.method == "POST":
        action = request.POST.get("action")

        if action in {"load_existing", "export"}:
            export_form = ExportCardsForm(request.POST)
            if export_form.is_valid():
                rarity = _normalize_sales_rarity(export_form.cleaned_data["rarity"])
                max_cards = int(export_form.cleaned_data["max_cards"])
                request.session[SALES_SELECTED_RARITY_SESSION_KEY] = rarity
                selected_rarity = rarity
                current_excel = _current_sales_excel_path(rarity)

                if action == "load_existing":
                    if current_excel:
                        messages.success(request, f"Cargado Excel existente: {current_excel.name}")
                    else:
                        messages.info(request, "No existe Excel para esa rareza todavía. Usa 'Generar nuevo Excel'.")
                    _clear_confirm_state(request)
                else:
                    result = run_export_cards(PATHS, rarity=rarity, max_cards=max_cards)
                    expected_excel = excel_path_for_rarity(PATHS, rarity)
                    if result.exit_code == 0 and expected_excel.exists():
                        current_excel = expected_excel
                        _clear_confirm_state(request)
                        export_result = None
                        messages.success(request, f"Excel generado: {expected_excel.name}")
                    else:
                        export_result = result
                        messages.error(request, "La exportación falló. Revisa el log.")
            else:
                messages.error(request, "Formulario de exportación inválido.")

        elif action == "save_prices":
            if not current_excel:
                messages.error(request, "Primero carga o genera un Excel para la rareza seleccionada.")
            else:
                _clear_confirm_state(request)
                updates = save_prices(current_excel, request.POST)
                messages.success(request, f"Precios guardados en Excel ({updates} filas actualizadas).")

        elif action == "reset_prices":
            if not current_excel:
                messages.error(request, "Primero carga o genera un Excel para la rareza seleccionada.")
            else:
                _clear_confirm_state(request)
                cleared = reset_prices(current_excel)
                messages.success(request, f"Precios reseteados ({cleared} filas limpiadas).")

        elif action in {"start_confirm", "start_confirm_inline"}:
            if not current_excel:
                messages.error(request, "Primero carga o genera un Excel para la rareza seleccionada.")
            else:
                save_prices(current_excel, request.POST)
                rows = load_sales_rows(current_excel)
                ready_rows = rows_ready_to_sell(rows)
                selected_rows = [int(v) for v in request.POST.getlist("selected_rows") if str(v).isdigit()]
                if not selected_rows:
                    selected_rows = [int(r["row"]) for r in ready_rows]

                if not selected_rows:
                    messages.error(request, "No hay cartas seleccionadas para confirmar ventas.")
                else:
                    request.session[SALES_CONFIRM_STATE_SESSION_KEY] = {
                        "rarity": selected_rarity,
                        "queue": selected_rows,
                        "index": 0,
                        "results": [],
                    }
                    messages.success(request, "Confirmación paso a paso iniciada.")

        elif action in {"confirm_step_sell", "confirm_step_skip"}:
            if not current_excel:
                messages.error(request, "No hay Excel cargado para confirmar.")
            else:
                rows = load_sales_rows(current_excel)
                _advance_confirmation_step(request, rows, action)

        elif action in {"confirm_step_cancel", "confirm_step_finish"}:
            _clear_confirm_state(request)
            messages.info(request, "Confirmación finalizada.")

    rows = load_sales_rows(current_excel) if current_excel else []
    ready_rows = rows_ready_to_sell(rows)

    confirm_state = _get_confirm_state(request)
    confirmation_item = None
    confirmation_results = []
    confirmation_progress = None
    confirmation_done = False

    if confirm_state and confirm_state.get("rarity") == selected_rarity:
        queue = [int(v) for v in confirm_state.get("queue", [])]
        queue_index = int(confirm_state.get("index", 0))
        confirmation_results = list(confirm_state.get("results", []))
        if queue_index < len(queue):
            row_map = {int(r["row"]): r for r in rows}
            confirmation_item = row_map.get(queue[queue_index])
            confirmation_progress = f"{queue_index + 1}/{len(queue)}"
        else:
            confirmation_done = True

        ok = sum(1 for r in confirmation_results if r.get("status") == "ok")
        fail = sum(1 for r in confirmation_results if r.get("status") == "fail")
        skip = sum(1 for r in confirmation_results if r.get("status") == "skip")
        execution_result = {
            "ok": ok,
            "fail": fail,
            "skip": skip,
            "items": confirmation_results,
        }

    return render(
        request,
        "dashboard/sales.html",
        {
            "export_form": export_form,
            "export_result": export_result,
            "execution_result": execution_result,
            "rows": rows,
            "excel_exists": bool(current_excel),
            "selected_rarity": selected_rarity,
            "confirmation_item": confirmation_item,
            "confirmation_progress": confirmation_progress,
            "confirmation_done": confirmation_done,
        },
    )


def sales_download_excel(request):
    selected_rarity = _normalize_sales_rarity(request.session.get(SALES_SELECTED_RARITY_SESSION_KEY))
    current_excel = _current_sales_excel_path(selected_rarity)
    if not current_excel or not current_excel.exists():
        raise Http404("No hay Excel exportado todavía")
    return FileResponse(
        current_excel.open("rb"),
        as_attachment=True,
        filename=current_excel.name,
    )


def auctions_list(request):
    """Vista para mostrar las ofertas/subastas activas de La Liga."""
    import listar_subastas
    
    auctions = []
    error = None
    loading = False
    
    if request.method == "POST" or request.GET.get("load"):
        loading = True
        try:
            # Filtros desde el formulario
            team_filters = request.POST.getlist("teams") or request.GET.getlist("teams") or None
            rarity = request.POST.get("rarity") or request.GET.get("rarity") or "rare"
            
            # Fetch auctions
            auctions = listar_subastas.fetch_la_liga_rare_auctions(
                team_filters=team_filters if team_filters else None,
                rarity=rarity
            )
            
            # Ordenar por fecha de fin
            auctions.sort(key=lambda x: x['end_date'])
            
            messages.success(request, f"✅ Encontradas {len(auctions)} subastas activas")
        except Exception as e:
            error = str(e)
            messages.error(request, f"Error al cargar subastas: {error}")
    
    # Equipos disponibles para filtro
    available_teams = listar_subastas.LA_LIGA_TEAM_SLUGS
    
    return render(
        request,
        "dashboard/auctions.html",
        {
            "auctions": auctions,
            "error": error,
            "loading": loading,
            "available_teams": available_teams,
        },
    )


def offers_received(request):
    """Muestra las ofertas recibidas pendientes."""
    import listar_ofertas_recibidas

    offers = []
    eth_rate = 0
    error = None
    try:
        offers, eth_rate = listar_ofertas_recibidas.fetch_pending_offers_received()
    except Exception as e:
        error = str(e)

    return render(
        request,
        "dashboard/offers_received.html",
        {"offers": offers, "error": error, "total": len(offers), "eth_rate": eth_rate},
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
