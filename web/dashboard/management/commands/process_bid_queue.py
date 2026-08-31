import time

import listar_subastas
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from dashboard.models import BidBatchItem, BidBatchJob
from dashboard.views import PATHS
from web_services.process_runner import BidRequest, bid_error_message, run_bid_scheduler


def _market_status(row):
    if not row:
        return {}
    return {
        "has_bid": bool(row.get("has_bid")),
        "is_winning": bool(row.get("is_winning")),
        "is_outbid": bool(row.get("is_outbid")),
        "bid_position": row.get("bid_position"),
        "bid_eur": row.get("bid_eur"),
        "my_bid_eur": row.get("my_bid_eur"),
    }


def process_next_job():
    with transaction.atomic():
        job = BidBatchJob.objects.select_for_update().filter(status=BidBatchJob.Status.QUEUED).first()
        if not job:
            return None
        job.status = BidBatchJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=("status", "started_at"))

    successes = failures = 0
    for item in job.items.all():
        item.status = BidBatchItem.Status.RUNNING
        item.save(update_fields=("status",))
        try:
            result = run_bid_scheduler(
                PATHS,
                BidRequest(
                    identifier=item.auction_id, euros=str(item.euros), hora="",
                    now=True, sniper=False, background=False, use_credit=item.use_credit,
                    currency=item.currency,
                ),
            )
            if result.exit_code == 0:
                item.status = BidBatchItem.Status.SUCCEEDED
                successes += 1
            else:
                item.status = BidBatchItem.Status.FAILED
                item.error = bid_error_message(result)[:2000]
                failures += 1
        except Exception as exc:
            item.status = BidBatchItem.Status.FAILED
            item.error = f"Error inesperado al enviar la puja: {exc}"[:2000]
            failures += 1
        item.save(update_fields=("status", "error"))

    succeeded_items = list(job.items.filter(status=BidBatchItem.Status.SUCCEEDED))
    if succeeded_items:
        # Sorare puede tardar un instante en reflejar la última puja. Esta consulta
        # es dirigida y no recorre el mercado completo.
        time.sleep(1)
        try:
            refreshed = listar_subastas.refresh_cached_auction_ids(
                [item.auction_id for item in succeeded_items]
            )
            missing = [
                item.auction_id for item in succeeded_items
                if not (refreshed.get(item.auction_id) or {}).get("has_bid")
            ]
            if missing:
                time.sleep(2)
                refreshed.update(listar_subastas.refresh_cached_auction_ids(missing))
            for item in succeeded_items:
                item.market_status = _market_status(refreshed.get(item.auction_id))
                item.save(update_fields=("market_status",))
        except Exception as exc:
            # La puja ya ha sido enviada: un fallo de refresco no cambia su resultado.
            for item in succeeded_items:
                item.market_status = {"refresh_error": str(exc)[:500]}
                item.save(update_fields=("market_status",))

    job.success_count = successes
    job.failure_count = failures
    job.finished_at = timezone.now()
    if failures == 0:
        job.status = BidBatchJob.Status.SUCCEEDED
    elif successes == 0:
        job.status = BidBatchJob.Status.FAILED
    else:
        job.status = BidBatchJob.Status.PARTIAL
    job.save(update_fields=("status", "success_count", "failure_count", "finished_at"))
    return job


class Command(BaseCommand):
    help = "Procesa en segundo plano los lotes de pujas pendientes"

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Permanecer escuchando nuevos lotes")

    def handle(self, *args, **options):
        # Una caída en mitad de una operación deja el resultado incierto. Por seguridad
        # no se reintenta automáticamente y se evita así una puja duplicada.
        interrupted = BidBatchJob.objects.filter(status=BidBatchJob.Status.RUNNING)
        for job in interrupted:
            job.items.filter(status__in=(BidBatchItem.Status.QUEUED, BidBatchItem.Status.RUNNING)).update(
                status=BidBatchItem.Status.FAILED,
                error="El proceso se interrumpió; comprueba la puja en Sorare antes de volver a intentarlo.",
            )
            job.status = BidBatchJob.Status.FAILED
            job.failure_count = job.items.filter(status=BidBatchItem.Status.FAILED).count()
            job.finished_at = timezone.now()
            job.save(update_fields=("status", "failure_count", "finished_at"))

        while True:
            processed = process_next_job()
            if not options["watch"]:
                break
            if processed is None:
                time.sleep(2)
