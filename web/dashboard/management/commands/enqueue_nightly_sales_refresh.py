from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from dashboard.models import SalesRefreshJob


RARITIES = ("limited", "rare", "super_rare")


class Command(BaseCommand):
    help = "Encola la actualización nocturna del inventario y sus publicaciones"

    def handle(self, *args, **options):
        user = (
            get_user_model().objects.filter(username__iexact="burguis").first()
            or get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        )
        if not user:
            self.stderr.write("No existe un usuario local con el que crear la actualización.")
            return

        for rarity in RARITIES:
            active = SalesRefreshJob.objects.filter(
                rarity=rarity,
                status__in=(SalesRefreshJob.Status.QUEUED, SalesRefreshJob.Status.RUNNING),
            ).exists()
            if active:
                self.stdout.write(f"{rarity}: ya existe una actualización pendiente.")
                continue
            job = SalesRefreshJob.objects.create(
                user=user,
                rarity=rarity,
                progress_label="En cola para actualización nocturna",
            )
            self.stdout.write(f"{rarity}: actualización #{job.pk} encolada.")
