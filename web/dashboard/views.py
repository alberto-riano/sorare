from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse

from django.contrib import messages
from django.core.paginator import Paginator
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

from .forms import BatchBidForm, BidScheduleForm, ExportCardsForm, InlineBidForm, TelegramSettingsForm

REPO_ROOT = Path(__file__).resolve().parents[2]
PATHS = SorarePaths(repo_root=REPO_ROOT)
SALES_SELECTED_RARITY_SESSION_KEY = "sales_selected_rarity"
SALES_CONFIRM_STATE_SESSION_KEY = "sales_confirm_state"
SALES_DURATION_DAYS = 2

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
            "title": "Ofertas Recibidas",
            "icon": "fas fa-envelope-open-text",
            "description": "Consulta las ofertas pendientes que has recibido por tus cartas.",
            "url": "offers_received",
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
                ),
            )
            if result.exit_code == 0:
                messages.success(request, f"Puja de {data['euros']:.2f} € enviada correctamente.")
            else:
                detail = result.stderr or result.stdout or "Error desconocido"
                messages.error(request, f"La puja no se pudo completar: {detail[-500:]}")
            return redirect("auctions_list")
        messages.error(request, "No se envió la puja: revisa el importe y la confirmación.")

    if request.method == "POST" and request.POST.get("action") == "place_batch_bids":
        batch_form = BatchBidForm(request.POST)
        if batch_form.is_valid():
            successful = 0
            failures = []
            for index, data in enumerate(batch_form.cleaned_data["bids"], start=1):
                result = run_bid_scheduler(
                    PATHS,
                    BidRequest(
                        identifier=data["auction_id"], euros=str(data["euros"]), hora="",
                        now=True, sniper=False, background=False,
                        use_credit=bool(data["use_credit"]),
                    ),
                )
                if result.exit_code == 0:
                    successful += 1
                else:
                    failures.append(str(index))
            if successful:
                messages.success(request, f"Se enviaron correctamente {successful} pujas.")
            if failures:
                messages.error(request, f"Fallaron {len(failures)} pujas del resumen (posiciones {', '.join(failures)}).")
            return redirect("auctions_list")
        messages.error(request, "No se envió ninguna puja: revisa el resumen y vuelve a confirmar.")

    try:
        if request.method == "POST" and request.POST.get("action") == "refresh_market":
            listar_subastas.refresh_auction_cache(force_full=True)
        team_filters = request.POST.getlist("teams") or request.GET.getlist("teams") or None
        auctions = listar_subastas.fetch_la_liga_rare_auctions(
            team_filters=team_filters if team_filters else None,
            rarity="rare",
            season_year=2026,
        )
        loading = False
        if request.method == "POST" and request.POST.get("action") == "refresh_market":
            messages.success(request, f"Mercado completo actualizado: {len(auctions)} subastas activas")
    except Exception as e:
        error = str(e)
        messages.error(request, f"Error al cargar subastas: {error}")
    
    available_teams = sorted({auction['team'] for auction in auctions})
    available_positions = sorted({auction['position'] for auction in auctions})
    filter_player = request.GET.get('player', '').strip()
    filter_team = request.GET.get('team', '').strip()
    filter_position = request.GET.get('position', '').strip()
    filter_status = request.GET.get('status', '').strip()
    filter_max_price = request.GET.get('max_price', '').strip()
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
    if filter_team:
        filtered_auctions = [row for row in filtered_auctions if row['team'] == filter_team]
    if filter_position:
        filtered_auctions = [row for row in filtered_auctions if row['position'] == filter_position]
    if filter_status == 'winning':
        filtered_auctions = [row for row in filtered_auctions if row['is_winning']]
    elif filter_status == 'outbid':
        filtered_auctions = [row for row in filtered_auctions if row['is_outbid']]
    try:
        max_price = float(filter_max_price) if filter_max_price else None
    except ValueError:
        max_price = None
    if max_price is not None:
        filtered_auctions = [row for row in filtered_auctions if row['bid_eur'] is not None and row['bid_eur'] <= max_price]

    madrid = ZoneInfo('Europe/Madrid')
    for auction in filtered_auctions:
        end_at = datetime.fromisoformat(auction['end_date'].replace('Z', '+00:00')).astimezone(madrid)
        auction['end_date_madrid'] = end_at.strftime('%d/%m/%Y %H:%M')

    paginator = Paginator(filtered_auctions, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)
    sort_query_params = query_params.copy()
    sort_query_params.pop('end_order', None)

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
            "filter_team": filter_team,
            "filter_position": filter_position,
            "filter_status": filter_status,
            "filter_max_price": filter_max_price,
            "end_order": end_order,
            "pagination_query": query_params.urlencode(),
            "sort_query": sort_query_params.urlencode(),
            "error": error,
            "loading": loading,
            "season_label": "2026-2027",
        },
    )


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
        typename = deal.get("__typename")
        offer_type = deal.get("type")
        if typename == "TokenAuction":
            kind, icon, label = "auction", "fa-gavel", "Subasta"
        elif typename == "TokenOffer" and (deal.get("senderSide") or {}).get("anyCards") and (deal.get("receiverSide") or {}).get("anyCards"):
            kind, icon, label = "trade", "fa-right-left", "Intercambio entre managers"
        elif typename == "TokenPrimaryOffer" or offer_type in {"SINGLE_SALE_OFFER", "SINGLE_BUY_OFFER"}:
            kind, icon, label = "instant", "fa-bolt", "Compra instantánea"
        elif typename == "TokenOffer":
            kind, icon, label = "instant", "fa-handshake", "Compra a otro manager"
        else:
            kind, icon, label = "trade", "fa-right-left", "Operación entre managers"
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
