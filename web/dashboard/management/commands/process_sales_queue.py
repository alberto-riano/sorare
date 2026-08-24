import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.models import (
    BidBatchItem, MovementPaymentEvidence, MovementSnapshot, MovementSyncJob, SaleBatchItem, SaleBatchJob,
    PublicRewardSnapshot, PublicRewardSyncJob, SalesInventory, SalesRefreshJob,
)
from dashboard.views import PATHS
from web_services.movement_history import (
    apply_purchase_payment_evidence, collect_movement_history, collect_public_reward_history,
)
from web_services.process_runner import run_card_sale, sale_error_message
from web_services.sales_inventory import collect_sales_inventory


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
        stored_evidence = {
            item.auction_id: {
                "currency": item.currency,
                "used_credit": item.used_credit,
                "credit_percentage": item.credit_percentage,
                "source": item.source,
            }
            for item in MovementPaymentEvidence.objects.filter(auction_id__in=auction_ids)
        }
        apply_purchase_payment_evidence(
            movements,
            local_bids=local_bids,
            stored_evidence=stored_evidence,
        )

        # Conserva nuevas pruebas locales/restricciones antes de que Sorare las
        # purgue. Nunca sobrescribe una corrección manual o un porcentaje ya
        # confirmado.
        for movement in movements:
            auction_id = str(movement.get("auction_id") or "")
            if not auction_id or auction_id in stored_evidence:
                continue
            local = local_bids.get(auction_id) or {}
            card_proof = any(
                card.get("credit_purchase_restricted")
                for card in movement.get("received_cards") or movement.get("cards") or []
            )
            if not local and not card_proof:
                continue
            MovementPaymentEvidence.objects.get_or_create(
                auction_id=auction_id,
                defaults={
                    "currency": str(local.get("currency") or movement.get("currency") or ""),
                    "used_credit": bool(local.get("use_credit") or card_proof),
                    "source": "local_bid" if local else "sorare_card_restriction",
                },
            )
        MovementSnapshot.objects.update_or_create(
            user=job.user,
            defaults={"movements": movements, "refreshed_at": timezone.now(), "source_version": 13},
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
        job.progress_label = f"Buscando recompensas de {job.manager_slug}"
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
                "source_version": 1,
            },
        )
        job.movement_count = len(movements)
        job.progress_label = "Recompensas públicas actualizadas"
        job.status = PublicRewardSyncJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = PublicRewardSyncJob.Status.FAILED
        job.progress_label = "Actualización interrumpida"
        job.error = f"No se pudieron actualizar las recompensas públicas: {exc}"[:2000]
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
    help = "Actualiza inventario e historial y procesa ventas en segundo plano"

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Permanecer escuchando nuevos trabajos")

    def handle(self, *args, **options):
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
                process_next_refresh()
                or process_next_movement_sync()
                or process_next_public_reward_sync()
                or process_next_sale()
            )
            if not options["watch"]:
                break
            if processed is None:
                time.sleep(2)
