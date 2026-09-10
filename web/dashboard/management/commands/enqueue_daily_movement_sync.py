from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from dashboard.models import (
    MovementSnapshot,
    MovementSyncJob,
    PublicRewardSnapshot,
    PublicRewardSyncJob,
)


DEFAULT_START_DATE = date(2026, 8, 12)
PUBLIC_MANAGER_SLUG = "blasco93"


class Command(BaseCommand):
    help = "Encola la actualización incremental diaria de burguis y blasco93"

    def handle(self, *args, **options):
        private_snapshot = MovementSnapshot.objects.select_related("user").order_by("pk").first()
        user = private_snapshot.user if private_snapshot else (
            get_user_model().objects.filter(username__iexact="burguis").first()
            or get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        )
        if not user:
            self.stderr.write("No existe un usuario local con el que crear las sincronizaciones.")
            return

        private_active = MovementSyncJob.objects.filter(
            user=user,
            status__in=(MovementSyncJob.Status.QUEUED, MovementSyncJob.Status.RUNNING),
        ).exists()
        if private_active:
            self.stdout.write("burguis: ya existe una actualización pendiente.")
        else:
            job = MovementSyncJob.objects.create(
                user=user,
                requested_start_date=(private_snapshot.history_start_date if private_snapshot else DEFAULT_START_DATE),
                progress_label="En cola para actualización incremental diaria",
            )
            self.stdout.write(f"burguis: actualización #{job.pk} encolada.")

        public_snapshot = PublicRewardSnapshot.objects.filter(manager_slug=PUBLIC_MANAGER_SLUG).first()
        public_active = PublicRewardSyncJob.objects.filter(
            manager_slug=PUBLIC_MANAGER_SLUG,
            status__in=(PublicRewardSyncJob.Status.QUEUED, PublicRewardSyncJob.Status.RUNNING),
        ).exists()
        if public_active:
            self.stdout.write(f"{PUBLIC_MANAGER_SLUG}: ya existe una actualización pendiente.")
        else:
            job = PublicRewardSyncJob.objects.create(
                user=user,
                manager_slug=PUBLIC_MANAGER_SLUG,
                requested_start_date=(public_snapshot.history_start_date if public_snapshot else DEFAULT_START_DATE),
                progress_label="En cola para actualización incremental diaria",
            )
            self.stdout.write(f"{PUBLIC_MANAGER_SLUG}: actualización #{job.pk} encolada.")
