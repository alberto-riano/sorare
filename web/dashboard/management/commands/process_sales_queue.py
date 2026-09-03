import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.models import (
    AuctionRefreshJob, BidBatchItem, MovementSnapshot, MovementSyncJob, SaleBatchItem, SaleBatchJob,
    InstantPurchaseRefreshJob, InstantPurchaseSnapshot, OpportunityRefreshJob, OpportunitySnapshot,
    PublicRewardSnapshot, PublicRewardSyncJob,
    SalesInventory, SalesRefreshJob,
)
from dashboard.views import PATHS
from web_services.movement_history import collect_movement_history, collect_public_reward_history
from web_services.process_runner import run_card_sale, sale_error_message
from web_services.sales_inventory import collect_sales_inventory


def process_next_opportunity_refresh():
    with transaction.atomic():
        job = OpportunityRefreshJob.objects.select_for_update().filter(
            status=OpportunityRefreshJob.Status.QUEUED,
        ).first()
        if not job:
            return None
        job.status = OpportunityRefreshJob.Status.RUNNING
        job.started_at = timezone.now()
        job.progress_label = "Preparando mercado fijo de LaLiga"
        job.save(update_fields=("status", "started_at", "progress_label"))

    try:
        from web_services.opportunity_market import build_opportunity_rows, collect_opportunity_market

        def save_progress(processed, total, label):
            OpportunityRefreshJob.objects.filter(pk=job.pk).update(
                processed_count=processed,
                total_count=total,
                progress_label=label,
            )

        def save_catalog(team_catalog):
            OpportunityRefreshJob.objects.filter(pk=job.pk).update(team_catalog=team_catalog)

        payload = collect_opportunity_market(
            progress=save_progress,
            team_slugs=job.target_team_slugs,
            catalog_callback=save_catalog,
        )
        refreshed_at = timezone.now()
        selected_metadata = payload.get("metadata") or {}
        refreshed_team_slugs = set(selected_metadata.get("refreshed_team_slugs") or [])
        snapshot = OpportunitySnapshot.objects.filter(market_key="laliga-2026").first()
        previous_rows = list(snapshot.rows if snapshot else [])
        kept_rows = [row for row in previous_rows if row.get("team_slug") not in refreshed_team_slugs]
        merged_rows, merged_metadata = build_opportunity_rows(kept_rows + list(payload.get("rows") or []))
        previous_metadata = dict(snapshot.metadata if snapshot else {})
        team_updated_at = dict(previous_metadata.get("team_updated_at") or {})
        for team_slug in refreshed_team_slugs:
            team_updated_at[team_slug] = refreshed_at.isoformat()
        merged_metadata.update({
            "roster_players": len(merged_rows),
            "players_analyzed": len(merged_rows),
            "active_listings": sum(
                1 for row in merged_rows for rarity in ("limited", "rare")
                if (row.get(rarity) or {}).get("floor")
            ),
            "opportunities": sum(1 for row in merged_rows if row.get("recommended_rarity")),
            "team_catalog": selected_metadata.get("team_catalog") or previous_metadata.get("team_catalog") or [],
            "refreshed_team_slugs": sorted(
                set(previous_metadata.get("refreshed_team_slugs") or []).union(refreshed_team_slugs)
            ),
            "team_updated_at": team_updated_at,
        })
        OpportunitySnapshot.objects.update_or_create(
            market_key="laliga-2026",
            defaults={"rows": merged_rows, "metadata": merged_metadata, "refreshed_at": refreshed_at, "source_version": 1},
        )
        job.refresh_from_db(fields=("processed_count", "total_count"))
        job.player_count = selected_metadata.get("players_analyzed") or 0
        job.opportunity_count = selected_metadata.get("opportunities") or 0
        job.processed_count = max(job.processed_count, job.total_count)
        job.progress_label = "Oportunidades actualizadas"
        job.status = OpportunityRefreshJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = OpportunityRefreshJob.Status.FAILED
        job.progress_label = "Análisis interrumpido"
        job.error = f"No se pudieron calcular las oportunidades: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=(
        "status", "processed_count", "total_count", "player_count", "opportunity_count",
        "progress_label", "error", "finished_at",
    ))
    return job


def process_next_instant_purchase_refresh():
    with transaction.atomic():
        job = InstantPurchaseRefreshJob.objects.select_for_update().filter(
            status=InstantPurchaseRefreshJob.Status.QUEUED,
        ).first()
        if not job:
            return None
        job.status = InstantPurchaseRefreshJob.Status.RUNNING
        job.started_at = timezone.now()
        job.progress_label = "Preparando compras instantáneas de LaLiga"
        job.save(update_fields=("status", "started_at", "progress_label"))

    try:
        from web_services.instant_purchase_market import collect_instant_purchase_market

        def save_progress(processed, total, label):
            InstantPurchaseRefreshJob.objects.filter(pk=job.pk).update(
                processed_count=processed,
                total_count=total,
                progress_label=label,
            )

        def save_catalog(team_catalog):
            InstantPurchaseRefreshJob.objects.filter(pk=job.pk).update(team_catalog=team_catalog)

        payload = collect_instant_purchase_market(
            progress=save_progress,
            team_slugs=job.target_team_slugs,
            catalog_callback=save_catalog,
        )
        refreshed_at = timezone.now()
        selected_metadata = payload.get("metadata") or {}
        refreshed_team_slugs = set(selected_metadata.get("refreshed_team_slugs") or [])
        snapshot = InstantPurchaseSnapshot.objects.filter(
            market_key="laliga-rare-2026", source_version=2,
        ).first()
        previous_rows = list(snapshot.rows if snapshot else [])
        rows = [row for row in previous_rows if row.get("team_slug") not in refreshed_team_slugs]
        rows.extend(payload.get("rows") or [])
        rows.sort(key=lambda row: (row.get("saving_eur") is not None, row.get("saving_eur") or 0), reverse=True)
        previous_metadata = dict(snapshot.metadata if snapshot else {})
        team_updated_at = dict(previous_metadata.get("team_updated_at") or {})
        for team_slug in refreshed_team_slugs:
            team_updated_at[team_slug] = refreshed_at.isoformat()
        metadata = {
            **previous_metadata,
            **selected_metadata,
            "active_listings": len(rows),
            "favorable_listings": sum(1 for row in rows if row.get("is_favorable")),
            "refreshed_team_slugs": sorted(
                set(previous_metadata.get("refreshed_team_slugs") or []).union(refreshed_team_slugs)
            ),
            "team_updated_at": team_updated_at,
        }
        InstantPurchaseSnapshot.objects.update_or_create(
            market_key="laliga-rare-2026",
            defaults={"rows": rows, "metadata": metadata, "refreshed_at": refreshed_at, "source_version": 2},
        )
        job.refresh_from_db(fields=("processed_count", "total_count"))
        job.listing_count = len(payload.get("rows") or [])
        job.favorable_count = selected_metadata.get("favorable_listings") or 0
        job.processed_count = max(job.processed_count, job.total_count)
        job.progress_label = "Compras instantáneas actualizadas"
        job.status = InstantPurchaseRefreshJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = InstantPurchaseRefreshJob.Status.FAILED
        job.progress_label = "Análisis interrumpido"
        job.error = f"No se pudieron actualizar las compras instantáneas: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=(
        "status", "processed_count", "total_count", "listing_count", "favorable_count",
        "progress_label", "error", "finished_at",
    ))
    return job


def process_next_auction_refresh():
    with transaction.atomic():
        job = AuctionRefreshJob.objects.select_for_update().filter(
            status=AuctionRefreshJob.Status.QUEUED,
        ).first()
        if not job:
            return None
        job.status = AuctionRefreshJob.Status.RUNNING
        job.started_at = timezone.now()
        job.progress_label = (
            "Preparando el barrido completo" if job.mode == AuctionRefreshJob.Mode.FULL
            else "Preparando la actualización de pujas"
        )
        job.save(update_fields=("status", "started_at", "progress_label"))

    try:
        import listar_subastas

        def save_progress(processed, total, label):
            AuctionRefreshJob.objects.filter(pk=job.pk).update(
                processed_count=processed,
                total_count=total,
                progress_label=label,
            )

        if job.mode == AuctionRefreshJob.Mode.FULL:
            payload = listar_subastas.refresh_auction_cache(force_full=True, progress=save_progress)
            job.progress_label = "Mercado completo actualizado"
        else:
            payload = listar_subastas.refresh_cached_auction_prices(progress=save_progress)
            job.progress_label = "Pujas actuales actualizadas"
        job.refresh_from_db(fields=("processed_count", "total_count"))
        job.auction_count = len(payload.get("auctions") or [])
        job.new_cards_count = payload.get("new_cards_count", 0) if job.mode == AuctionRefreshJob.Mode.FULL else 0
        job.processed_count = max(job.processed_count, job.total_count)
        job.status = AuctionRefreshJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = AuctionRefreshJob.Status.FAILED
        job.progress_label = "Actualización interrumpida"
        job.error = f"No se pudo actualizar el mercado: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=(
        "status", "processed_count", "total_count", "auction_count", "new_cards_count",
        "progress_label", "error", "finished_at",
    ))
    return job


def process_next_refresh():
    with transaction.atomic():
        job = SalesRefreshJob.objects.select_for_update().filter(status=SalesRefreshJob.Status.QUEUED).first()
        if not job:
            return None
        job.status = SalesRefreshJob.Status.RUNNING
        job.started_at = timezone.now()
        job.progress_label = "Descargando cartas de Sorare"
        job.save(update_fields=("status", "started_at", "progress_label"))

    try:
        def save_progress(processed, total, label):
            SalesRefreshJob.objects.filter(pk=job.pk).update(
                processed_count=processed,
                total_count=total,
                progress_label=label,
            )

        cards = collect_sales_inventory(job.rarity, progress=save_progress)
        SalesInventory.objects.update_or_create(
            rarity=job.rarity,
            defaults={"cards": cards, "refreshed_at": timezone.now()},
        )
        job.card_count = len(cards)
        job.processed_count = len(cards)
        job.total_count = len(cards)
        job.progress_label = "Inventario actualizado"
        job.status = SalesRefreshJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = SalesRefreshJob.Status.FAILED
        job.progress_label = "Actualización interrumpida"
        job.error = f"No se pudo actualizar el inventario: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=(
        "status", "card_count", "processed_count", "total_count",
        "progress_label", "error", "finished_at",
    ))
    return job


def process_next_movement_sync():
    with transaction.atomic():
        job = MovementSyncJob.objects.select_for_update().filter(status=MovementSyncJob.Status.QUEUED).first()
        if not job:
            return None
        job.status = MovementSyncJob.Status.RUNNING
        job.started_at = timezone.now()
        job.progress_label = "Conectando con el historial de Sorare"
        job.save(update_fields=("status", "started_at", "progress_label"))

    try:
        def save_progress(processed, label):
            MovementSyncJob.objects.filter(pk=job.pk).update(
                processed_count=processed,
                progress_label=label,
            )

        movements = collect_movement_history(progress=save_progress)
        auction_ids = {
            str(movement.get("auction_id") or "")
            for movement in movements
            if movement.get("auction_id")
        }
        local_bids = {}
        for item in BidBatchItem.objects.filter(
            auction_id__in=auction_ids,
            status=BidBatchItem.Status.SUCCEEDED,
        ).select_related("job").order_by("-job__created_at"):
            local_bids.setdefault(item.auction_id, {
                "currency": item.currency,
                "use_credit": item.use_credit,
            })
        for movement in movements:
            auction_id = str(movement.get("auction_id") or "")
            local = local_bids.get(auction_id) or {}
            if not movement.get("currency") and str(local.get("currency") or "") in {"EUR", "ETH"}:
                movement["currency"] = local["currency"]
        MovementSnapshot.objects.update_or_create(
            user=job.user,
            defaults={"movements": movements, "refreshed_at": timezone.now(), "source_version": 17},
        )
        job.movement_count = len(movements)
        job.progress_label = "Historial actualizado"
        job.status = MovementSyncJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = MovementSyncJob.Status.FAILED
        job.progress_label = "Actualización interrumpida"
        job.error = f"No se pudo actualizar el historial: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=(
        "status", "movement_count", "processed_count", "progress_label", "error", "finished_at",
    ))
    return job


def process_next_public_reward_sync():
    with transaction.atomic():
        job = PublicRewardSyncJob.objects.select_for_update().filter(
            status=PublicRewardSyncJob.Status.QUEUED,
        ).first()
        if not job:
            return None
        job.status = PublicRewardSyncJob.Status.RUNNING
        job.started_at = timezone.now()
        job.progress_label = f"Buscando movimientos de {job.manager_slug}"
        job.save(update_fields=("status", "started_at", "progress_label"))

    try:
        def save_progress(processed, label):
            PublicRewardSyncJob.objects.filter(pk=job.pk).update(
                processed_count=processed,
                progress_label=label,
            )

        result = collect_public_reward_history(job.manager_slug, progress=save_progress)
        movements = result["movements"]
        PublicRewardSnapshot.objects.update_or_create(
            manager_slug=job.manager_slug,
            defaults={
                "manager_nickname": result["manager_nickname"],
                "movements": movements,
                "refreshed_at": timezone.now(),
                "source_version": 4,
            },
        )
        job.movement_count = len(movements)
        job.progress_label = "Movimientos públicos actualizados"
        job.status = PublicRewardSyncJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = PublicRewardSyncJob.Status.FAILED
        job.progress_label = "Actualización interrumpida"
        job.error = f"No se pudieron actualizar los movimientos públicos: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=(
        "status", "movement_count", "processed_count", "progress_label", "error", "finished_at",
    ))
    return job


def _mark_cached_card_as_listed(item):
    inventory = SalesInventory.objects.filter(rarity=item.rarity).first()
    if not inventory:
        return
    cards = inventory.cards
    changed = False
    for card in cards:
        if card.get("asset_id") != item.asset_id:
            continue
        card.update({
            "active_listing": True,
            "active_offer_eur": float(item.euros),
            "active_offer_end": (timezone.now() + timedelta(days=item.duration_days)).isoformat(),
            "blocked": True,
            "blocked_reason": "Ya está a la venta",
        })
        changed = True
        break
    if changed:
        inventory.cards = cards
        inventory.save(update_fields=("cards",))


def process_next_sale():
    with transaction.atomic():
        job = SaleBatchJob.objects.select_for_update().filter(status=SaleBatchJob.Status.QUEUED).first()
        if not job:
            return None
        job.status = SaleBatchJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=("status", "started_at"))

    successes = failures = 0
    for item in job.items.all():
        item.status = SaleBatchItem.Status.RUNNING
        item.save(update_fields=("status",))
        try:
            result = run_card_sale(
                PATHS,
                asset_id=item.asset_id,
                euros=str(item.euros),
                duration_days=item.duration_days,
                minimum_offer_eur=(str(item.minimum_offer_eur) if item.minimum_offer_eur is not None else None),
            )
            if result.exit_code == 0:
                item.status = SaleBatchItem.Status.SUCCEEDED
                successes += 1
                _mark_cached_card_as_listed(item)
            else:
                item.status = SaleBatchItem.Status.FAILED
                item.error = sale_error_message(result)[:2000]
                failures += 1
        except Exception as exc:
            item.status = SaleBatchItem.Status.FAILED
            item.error = f"Error inesperado al poner la carta a la venta: {exc}"[:2000]
            failures += 1
        item.save(update_fields=("status", "error"))

    job.success_count = successes
    job.failure_count = failures
    job.finished_at = timezone.now()
    if failures == 0:
        job.status = SaleBatchJob.Status.SUCCEEDED
    elif successes == 0:
        job.status = SaleBatchJob.Status.FAILED
    else:
        job.status = SaleBatchJob.Status.PARTIAL
    job.save(update_fields=("status", "success_count", "failure_count", "finished_at"))
    return job


class Command(BaseCommand):
    help = "Actualiza mercado, inventario e historial y procesa ventas en segundo plano"

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Permanecer escuchando nuevos trabajos")

    def handle(self, *args, **options):
        AuctionRefreshJob.objects.filter(status=AuctionRefreshJob.Status.RUNNING).update(
            status=AuctionRefreshJob.Status.FAILED,
            error="La actualización se interrumpió; el mercado anterior sigue disponible.",
            finished_at=timezone.now(),
        )
        interrupted_refreshes = SalesRefreshJob.objects.filter(status=SalesRefreshJob.Status.RUNNING)
        interrupted_refreshes.update(
            status=SalesRefreshJob.Status.FAILED,
            error="La actualización se interrumpió; el inventario anterior sigue disponible.",
            finished_at=timezone.now(),
        )
        MovementSyncJob.objects.filter(status=MovementSyncJob.Status.RUNNING).update(
            status=MovementSyncJob.Status.FAILED,
            error="La actualización se interrumpió; el historial anterior sigue disponible.",
            finished_at=timezone.now(),
        )
        PublicRewardSyncJob.objects.filter(status=PublicRewardSyncJob.Status.RUNNING).update(
            status=PublicRewardSyncJob.Status.FAILED,
            error="La actualización se interrumpió; las recompensas anteriores siguen disponibles.",
            finished_at=timezone.now(),
        )
        OpportunityRefreshJob.objects.filter(status=OpportunityRefreshJob.Status.RUNNING).update(
            status=OpportunityRefreshJob.Status.FAILED,
            error="El análisis se interrumpió; el snapshot anterior sigue disponible.",
            finished_at=timezone.now(),
        )
        InstantPurchaseRefreshJob.objects.filter(status=InstantPurchaseRefreshJob.Status.RUNNING).update(
            status=InstantPurchaseRefreshJob.Status.FAILED,
            error="El análisis se interrumpió; el snapshot anterior sigue disponible.",
            finished_at=timezone.now(),
        )
        for job in SaleBatchJob.objects.filter(status=SaleBatchJob.Status.RUNNING):
            job.items.filter(status__in=(SaleBatchItem.Status.QUEUED, SaleBatchItem.Status.RUNNING)).update(
                status=SaleBatchItem.Status.FAILED,
                error="El proceso se interrumpió; comprueba la carta en Sorare antes de volver a intentarlo.",
            )
            job.status = SaleBatchJob.Status.FAILED
            job.failure_count = job.items.filter(status=SaleBatchItem.Status.FAILED).count()
            job.finished_at = timezone.now()
            job.save(update_fields=("status", "failure_count", "finished_at"))

        while True:
            processed = (
                process_next_auction_refresh()
                or process_next_opportunity_refresh()
                or process_next_instant_purchase_refresh()
                or process_next_refresh()
                or process_next_movement_sync()
                or process_next_public_reward_sync()
                or process_next_sale()
            )
            if not options["watch"]:
                break
            if processed is None:
                time.sleep(2)
