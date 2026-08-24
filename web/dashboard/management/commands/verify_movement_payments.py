from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand

from sorare_utils import build_headers, graphql_request
from web_services.movement_history import PAYMENT_ACCOUNTS_QUERY, PAYMENT_HISTORY_START, _money


TARGETS = {
    "Bid:bfa83679-3fe7-4be5-a8d6-ce8ff4196900": ("Rodrygo #16", Decimal("24.03")),
    "Bid:9be628bf-e169-4905-91e5-8830f1a519e4": ("L. Rioja #9", Decimal("19.01")),
}


class Command(BaseCommand):
    help = "Comprueba de forma segura la moneda y los créditos de Rodrygo #16 y L. Rioja #9"

    def handle(self, *args, **options):
        headers = build_headers()
        matches = defaultdict(lambda: defaultdict(lambda: Decimal("0")))

        for api_currency, display_currency in (("EUR", "EUR"), ("WEI", "ETH")):
            cursor = None
            pages = 0
            while pages < 20:
                pages += 1
                data = graphql_request(PAYMENT_ACCOUNTS_QUERY, {
                    "first": 100,
                    "after": cursor,
                    "currencies": [api_currency],
                    "startDate": PAYMENT_HISTORY_START,
                }, headers=headers)
                connection = ((data.get("currentUser") or {}).get("accountEntries") or {})
                for entry in connection.get("nodes") or []:
                    operation_id = str((entry.get("tokenOperation") or {}).get("id") or "")
                    if (
                        operation_id in TARGETS
                        and not entry.get("provisional")
                        and entry.get("aasmState") == "CONFIRMED"
                    ):
                        matches[operation_id][display_currency] += Decimal(
                            str(_money(entry.get("amounts"))["eur"]),
                        )
                page_info = connection.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")

        self.stdout.write("Verificación privada de pagos (sin credenciales):")
        failed = False
        for operation_id, (label, gross_eur) in TARGETS.items():
            evidence = matches.get(operation_id) or {}
            currencies = sorted(evidence)
            if currencies == ["EUR"]:
                paid_eur = evidence["EUR"]
                credits_eur = max(gross_eur - paid_eur, Decimal("0"))
                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK {label}: EUR · precio {gross_eur:.2f} € · "
                        f"saldo {paid_eur:.2f} € · créditos {credits_eur:.2f} €",
                    ),
                )
            elif currencies == ["ETH"]:
                self.stdout.write(self.style.WARNING(f"AVISO {label}: pago registrado en ETH"))
                failed = True
            elif currencies:
                self.stdout.write(self.style.ERROR(f"ERROR {label}: aparecen varias monedas: {', '.join(currencies)}"))
                failed = True
            else:
                self.stdout.write(self.style.ERROR(f"ERROR {label}: Sorare no devolvió ningún apunte de pago"))
                failed = True

        if failed:
            raise SystemExit(1)
