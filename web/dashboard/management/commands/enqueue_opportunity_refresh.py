from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from dashboard.models import OpportunityRefreshJob


class Command(BaseCommand):
    help = "Encola el análisis periódico de oportunidades si no hay otro activo"

    def handle(self, *args, **options):
        active = OpportunityRefreshJob.objects.filter(
            status__in=(OpportunityRefreshJob.Status.QUEUED, OpportunityRefreshJob.Status.RUNNING),
        ).first()
        if active:
            self.stdout.write(f"Ya existe el trabajo activo {active.pk}")
            return
        user = get_user_model().objects.order_by("id").first()
        if not user:
            self.stdout.write("No hay usuarios; no se encola el análisis")
            return
        job = OpportunityRefreshJob.objects.create(user=user)
        self.stdout.write(self.style.SUCCESS(f"Análisis de oportunidades encolado: {job.pk}"))
