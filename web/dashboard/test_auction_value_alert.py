from datetime import datetime, timedelta, timezone
from unittest import TestCase

from auction_value_alert import candidate_auctions, next_bid_eur


class AuctionValueAlertTests(TestCase):
    def test_next_bid_eur_supports_eur_cents(self):
        self.assertEqual(next_bid_eur("1234", "EUR", 200_000), 12.34)

    def test_next_bid_eur_supports_wei_with_sorare_rate(self):
        self.assertEqual(next_bid_eur("5000000000000000", "WEI", 200_000), 10.0)

    def test_candidates_only_enter_once_inside_configured_window(self):
        now = datetime(2026, 9, 2, 10, tzinfo=timezone.utc)
        rows = [
            {"auction_id": "too-early", "end_date": (now + timedelta(minutes=11)).isoformat()},
            {"auction_id": "candidate", "end_date": (now + timedelta(minutes=9, seconds=50)).isoformat()},
            {"auction_id": "finished", "end_date": (now - timedelta(seconds=1)).isoformat()},
            {"auction_id": "seen", "end_date": (now + timedelta(minutes=5)).isoformat()},
        ]

        result = candidate_auctions(rows, now, 10, {"seen": now.isoformat()})

        self.assertEqual([row["auction_id"] for row in result], ["candidate"])
