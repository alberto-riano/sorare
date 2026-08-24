from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand

from sorare_utils import build_headers, graphql_request
from web_services.movement_history import (
    COMPLETED_TRADES_QUERY,
    CONVERSION_CREDITS_QUERY,
    PAYMENT_HISTORY_START,
    _money,
)


TARGETS = {
    "Bid:bfa83679-3fe7-4be5-a8d6-ce8ff4196900": {
        "label": "Rodrygo #16",
        "auction": "EnglishAuction:569601ec-31de-49b9-9500-8e30e05632f5",
        "gross": Decimal("24.03"),
    },
    "Bid:9be628bf-e169-4905-91e5-8830f1a519e4": {
        "label": "L. Rioja #9",
        "auction": "EnglishAuction:ae63cf9d-ca4c-4579-b813-bb5d487e4045",
        "gross": Decimal("19.01"),
    },
    "Bid:54fdb23e-0162-46aa-aa00-172a33ef2f64": {
        "label": "Frenkie de Jong #18 (control)",
        "auction": "EnglishAuction:76b77ada-1c99-4c49-bfd5-6d84d321f108",
        "gross": Decimal("40.69"),
    },
}


ACCOUNT_ENTRIES_QUERY = """
query VerifyMovementAccounts(
  $first: Int!
  $after: String
  $currencyType: Currency
  $startDate: ISO8601DateTime
) {
  currentUser {
    accountEntries(
      first: $first
      after: $after
      entryType: [PAYMENT]
      currencyType: $currencyType
      startDate: $startDate
      sortType: DESC
    ) {
      nodes {
        id date entryType provisional internal aasmState
        amounts { eurCents wei referenceCurrency }
        account { accountable { __typename ... on FiatWalletAccount { currency } } }
        tokenOperation {
          __typename
          ... on TokenBid { id }
          ... on TokenOffer { id }
          ... on TokenPrimaryOffer { id }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


FIAT_PAYMENTS_QUERY = """
query VerifyFiatPayments($first: Int!, $after: String, $startDate: ISO8601DateTime) {
  currentUser {
    spentFiatPaymentIntents(
      first: $first
      after: $after
      startDate: $startDate
      sortType: DESC
    ) {
      nodes {
        id aasmState amount fiat fiatAmount fiatCurrency spentAt
        tokenOperation {
          __typename
          ... on TokenBid { id }
          ... on TokenOffer { id }
          ... on TokenPrimaryOffer { id }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


AUCTION_NOTIFICATIONS_QUERY = """
query VerifyAuctionNotifications($first: Int!, $after: String) {
  currentUser {
    anyNotifications(first: $first, after: $after, sports: [FOOTBALL]) {
      nodes {
        __typename
        ... on AuctionNotification {
          id createdAt title body
          tokenAuction { id }
          tokenBid {
            id fiatPayment
            amounts { eurCents wei referenceCurrency }
            conversionCredits {
              id status percentageDiscount singleUse
              totalDiscount { eurCents wei referenceCurrency }
              purchase { __typename ... on TokenAuction { id } }
            }
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def _pages(query, connection_name, variables, headers, *, max_pages=20):
    cursor = None
    for _page in range(max_pages):
        page_variables = dict(variables, after=cursor)
        data = graphql_request(query, page_variables, headers=headers)
        connection = ((data.get("currentUser") or {}).get(connection_name) or {})
        yield from connection.get("nodes") or []
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return
        cursor = page_info.get("endCursor")


def _credit_summary(credit):
    purchase = credit.get("purchase") or {}
    discount = Decimal(str(_money(credit.get("totalDiscount"))["eur"]))
    return {
        "status": credit.get("status"),
        "percentage": credit.get("percentageDiscount"),
        "single_use": credit.get("singleUse"),
        "discount_eur": discount,
        "purchase": purchase.get("id"),
    }


class Command(BaseCommand):
    help = "Compara todas las fuentes privadas de pago para Rodrygo, L. Rioja y De Jong"

    def handle(self, *args, **options):
        headers = build_headers()
        evidence = {
            bid_id: {
                "accounts": defaultdict(list),
                "fiat_intents": [],
                "trade_credits": [],
                "notification_credits": [],
                "global_credits": [],
            }
            for bid_id in TARGETS
        }

        for currency_type in (None, "FIAT", "ETH"):
            label = currency_type or "SIN_FILTRO"
            for entry in _pages(
                ACCOUNT_ENTRIES_QUERY,
                "accountEntries",
                {"first": 100, "currencyType": currency_type, "startDate": PAYMENT_HISTORY_START},
                headers,
            ):
                bid_id = str((entry.get("tokenOperation") or {}).get("id") or "")
                if bid_id not in evidence:
                    continue
                accountable = ((entry.get("account") or {}).get("accountable") or {})
                evidence[bid_id]["accounts"][label].append({
                    "date": entry.get("date"),
                    "account": accountable.get("__typename"),
                    "account_currency": accountable.get("currency"),
                    "reference": (entry.get("amounts") or {}).get("referenceCurrency"),
                    "eur": Decimal(str(_money(entry.get("amounts"))["eur"])),
                    "eth": Decimal(str(_money(entry.get("amounts"))["eth"])),
                })

        for payment in _pages(
            FIAT_PAYMENTS_QUERY,
            "spentFiatPaymentIntents",
            {"first": 100, "startDate": PAYMENT_HISTORY_START},
            headers,
        ):
            bid_id = str((payment.get("tokenOperation") or {}).get("id") or "")
            if bid_id in evidence:
                evidence[bid_id]["fiat_intents"].append({
                    "state": payment.get("aasmState"),
                    "fiat": payment.get("fiat"),
                    "fiat_amount": payment.get("fiatAmount"),
                    "fiat_currency": payment.get("fiatCurrency"),
                    "spent_at": payment.get("spentAt"),
                    "wei": payment.get("amount"),
                })

        for trade in _pages(
            COMPLETED_TRADES_QUERY,
            "trades",
            {"first": 100},
            headers,
        ):
            bid = trade.get("bestBid") or {}
            bid_id = str(bid.get("id") or "")
            if bid_id in evidence:
                credits = list(bid.get("conversionCredits") or [])
                if not credits and bid.get("conversionCredit"):
                    credits = [bid["conversionCredit"]]
                evidence[bid_id]["trade_credits"] = [_credit_summary(item) for item in credits]

        for notification in _pages(
            AUCTION_NOTIFICATIONS_QUERY,
            "anyNotifications",
            {"first": 100},
            headers,
        ):
            bid = notification.get("tokenBid") or {}
            bid_id = str(bid.get("id") or "")
            if bid_id in evidence:
                evidence[bid_id]["notification_credits"] = [
                    _credit_summary(item) for item in bid.get("conversionCredits") or []
                ]

        for credit in _pages(
            CONVERSION_CREDITS_QUERY,
            "sportConversionCredits",
            {"first": 100},
            headers,
        ):
            purchase_id = str((credit.get("purchase") or {}).get("id") or "")
            for bid_id, target in TARGETS.items():
                if purchase_id == target["auction"]:
                    evidence[bid_id]["global_credits"].append(_credit_summary(credit))

        self.stdout.write("Diagnóstico privado comparado (sin token ni credenciales):")
        for bid_id, target in TARGETS.items():
            item = evidence[bid_id]
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{target['label']} · {target['gross']:.2f} €"))
            for filter_name in ("SIN_FILTRO", "FIAT", "ETH"):
                entries = item["accounts"].get(filter_name) or []
                rendered = "; ".join(
                    f"{entry['account']}/{entry['account_currency'] or '-'} "
                    f"ref={entry['reference']} eur={entry['eur']:.2f} eth={entry['eth']:.6f}"
                    for entry in entries
                ) or "ninguno"
                self.stdout.write(f"  Apuntes {filter_name}: {rendered}")
            self.stdout.write(f"  Intentos fiat: {item['fiat_intents'] or 'ninguno'}")
            self.stdout.write(f"  Créditos en transacción: {item['trade_credits'] or 'ninguno'}")
            self.stdout.write(f"  Créditos en notificación: {item['notification_credits'] or 'ninguno'}")
            self.stdout.write(f"  Créditos globales unidos: {item['global_credits'] or 'ninguno'}")
