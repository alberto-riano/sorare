import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.models import SaleBatchItem, SaleBatchJob, SalesInventory, SalesRefreshJob
from dashboard.views import PATHS
from web_services.process_runner import run_card_sale, sale_error_message
from web_services.sales_inventory import collect_sales_inventory


def process_next_refresh():
    with transaction.atomic():
        job = SalesRefreshJob.objects.select_for_update().filter(status=SalesRefreshJob.Status.QUEUED).first()
        if not job:
            return None
        job.status = SalesRefreshJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=("status", "started_at"))

    try:
        cards = collect_sales_inventory(job.rarity)
        SalesInventory.objects.update_or_create(
            rarity=job.rarity,
            defaults={"cards": cards, "refreshed_at": timezone.now()},
        )
        job.card_count = len(cards)
        job.status = SalesRefreshJob.Status.SUCCEEDED
    except Exception as exc:
        job.status = SalesRefreshJob.Status.FAILED
        job.error = f"No se pudo actualizar el inventario: {exc}"[:2000]
    job.finished_at = timezone.now()
    job.save(update_fields=("status", "card_count", "error", "finished_at"))
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
    help = "Actualiza el inventario y procesa ventas en segundo plano"

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Permanecer escuchando nuevos trabajos")

    def handle(self, *args, **options):
        interrupted_refreshes = SalesRefreshJob.objects.filter(status=SalesRefreshJob.Status.RUNNING)
        interrupted_refreshes.update(
            status=SalesRefreshJob.Status.FAILED,
            error="La actualización se interrumpió; el inventario anterior sigue disponible.",
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
            processed = process_next_refresh() or process_next_sale()
            if not options["watch"]:
                break
            if processed is None:
                time.sleep(2)
