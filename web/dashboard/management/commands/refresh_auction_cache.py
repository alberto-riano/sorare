from django.core.management.base import BaseCommand

from listar_subastas import refresh_auction_cache


class Command(BaseCommand):
    help = "Sincroniza la caché de subastas Rare de LaLiga 2026-2027"

    def add_arguments(self, parser):
        parser.add_argument("--full", action="store_true", help="Fuerza un barrido completo del mercado")

    def handle(self, *args, **options):
        payload = refresh_auction_cache(force_full=options["full"])
        self.stdout.write(self.style.SUCCESS(f"Disponibles: {len(payload['auctions'])} subastas"))
