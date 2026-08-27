from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dashboard.management.commands.process_sales_queue import process_next_instant_purchase_refresh
from dashboard.models import InstantPurchaseRefreshJob, InstantPurchaseSnapshot
from web_services.instant_purchase_market import (
    PLAYER_BATCH_SIZE,
    _live_offers_query,
    _recent_comparables,
    build_instant_purchase_rows,
)


class InstantPurchaseValuationTests(TestCase):
    def test_live_listing_query_stays_within_conservative_page_size(self):
        query, variables = _live_offers_query([("player-one", None)])

        self.assertEqual(PLAYER_BATCH_SIZE, 1)
        self.assertIn("first: 10", query)
        self.assertEqual(variables, {"slug0": "player-one", "after0": None})

    def test_history_keeps_recent_auctions_and_public_offers_only(self):
        now = datetime(2026, 8, 27, tzinfo=ZoneInfo("UTC"))
        base = {
            "amounts": {"eurCents": 2500},
            "card": {"seasonYear": 2026, "inSeasonEligible": True},
        }
        prices = [
            {**base, "date": "2026-08-26T10:00:00Z", "deal": {"__typename": "TokenAuction"}},
            {**base, "date": "2026-08-25T10:00:00Z", "deal": {"__typename": "TokenOffer", "type": "SINGLE_BUY_OFFER"}},
            {**base, "date": "2026-08-24T10:00:00Z", "deal": {"__typename": "TokenOffer", "type": "SINGLE_SALE_OFFER"}},
            {**base, "date": "2026-07-01T10:00:00Z", "deal": {"__typename": "TokenAuction"}},
        ]

        rows = _recent_comparables(prices, (0.92, 1.17, 1800), now=now)

        self.assertEqual([row["kind"] for row in rows], ["auction", "public"])

    def test_own_listing_is_excluded_and_rows_sort_by_absolute_difference(self):
        listings = [
            {"offer_id": "cheap", "player_slug": "one", "rarity": "rare", "price_eur": 50},
            {"offer_id": "floor", "player_slug": "one", "rarity": "rare", "price_eur": 80},
            {"offer_id": "expensive", "player_slug": "two", "rarity": "rare", "price_eur": 1000},
            {"offer_id": "expensive-floor", "player_slug": "two", "rarity": "rare", "price_eur": 1050},
        ]
        histories = {
            (slug, rarity): []
            for slug in ("one", "two")
            for rarity in ("limited", "rare")
        }

        rows, metadata = build_instant_purchase_rows(listings, histories)

        self.assertEqual(rows[0]["offer_id"], "expensive")
        self.assertEqual(rows[0]["saving_eur"], 50)
        cheap = next(row for row in rows if row["offer_id"] == "cheap")
        self.assertEqual(cheap["next_rare_floor"], 80)
        self.assertEqual(cheap["saving_eur"], 30)
        self.assertEqual(metadata["favorable_listings"], 2)


class InstantPurchasePageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="instant-user", password="test-pass")
        self.client.force_login(self.user)
        InstantPurchaseSnapshot.objects.create(
            rows=[{
                "offer_id": "offer-1", "card_slug": "player-2026-rare-1", "player": "Jugador Rare",
                "player_slug": "jugador-rare", "team": "Real Madrid", "team_slug": "real-madrid-madrid",
                "team_picture_url": "https://example.com/badge.png", "position": "Forward", "serial": 1,
                "price_eur": 50, "next_rare_floor": 80, "rare_sales_reference": 75,
                "rare_sales": [], "limited_floor": 15, "limited_sales_reference": 14,
                "limited_sales": [], "limited_parity_reference": 67.5, "estimated_value": 75,
                "saving_eur": 25, "saving_percent": 33.3, "confidence": "high", "is_favorable": True,
            }],
            metadata={"active_listings": 1, "favorable_listings": 1, "rare_limited_ratio": 4.5},
        )

    def test_page_renders_listing_shield_and_sorare_link(self):
        response = self.client.get(reverse("instant_purchases"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jugador Rare")
        self.assertContains(response, "https://example.com/badge.png")
        self.assertContains(response, "https://sorare.com/football/cards/player-2026-rare-1")
        self.assertContains(response, "+25,00 €")

    def test_filters_can_hide_non_matching_player(self):
        response = self.client.get(reverse("instant_purchases"), {"player": "otro"})

        self.assertEqual(response.context["filtered_count"], 0)
        self.assertNotContains(response, "player-2026-rare-1")

    @patch("web_services.instant_purchase_market.collect_instant_purchase_market")
    def test_worker_persists_snapshot(self, collect):
        collect.return_value = {
            "rows": [{"offer_id": "new", "team_slug": "real-madrid-madrid", "saving_eur": 12, "is_favorable": True}],
            "metadata": {"favorable_listings": 1, "refreshed_team_slugs": ["real-madrid-madrid"]},
        }
        job = InstantPurchaseRefreshJob.objects.create(
            user=self.user, target_team_slugs=["real-madrid-madrid"],
        )

        processed = process_next_instant_purchase_refresh()

        job.refresh_from_db()
        self.assertEqual(processed.pk, job.pk)
        self.assertEqual(job.status, InstantPurchaseRefreshJob.Status.SUCCEEDED)
        self.assertEqual(InstantPurchaseSnapshot.objects.get().rows[0]["offer_id"], "new")