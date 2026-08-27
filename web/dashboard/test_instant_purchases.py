from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dashboard.management.commands.process_sales_queue import process_next_instant_purchase_refresh
from dashboard.models import InstantPurchaseRefreshJob, InstantPurchaseSnapshot
from web_services.instant_purchase_market import (
    PRIMARY_OFFER_PAGE_SIZE,
    PRIMARY_OFFERS_QUERY,
    _normalize_primary_offers,
    _recent_comparables,
    build_instant_purchase_rows,
)


class InstantPurchaseValuationTests(TestCase):
    def test_live_listing_query_uses_sorare_primary_offers(self):
        self.assertEqual(PRIMARY_OFFER_PAGE_SIZE, 5)
        self.assertIn("livePrimaryOffers", PRIMARY_OFFERS_QUERY)
        self.assertNotIn("liveSingleSaleOffers", PRIMARY_OFFERS_QUERY)
        self.assertIn("instantBuyCampaign", PRIMARY_OFFERS_QUERY)

    def test_primary_offers_are_filtered_to_rare_laliga_cards(self):
        card = {
            "slug": "player-2026-rare-1", "assetId": "asset", "rarityTyped": "rare",
            "seasonYear": 2026, "serialNumber": 1, "inSeasonEligible": True,
            "anyPositions": ["Forward"],
            "anyPlayer": {"slug": "player", "displayName": "Player", "squaredPictureUrl": "player.png"},
            "anyTeam": {"slug": "team", "name": "Team", "pictureUrl": "team.png"},
        }
        offers = [{
            "id": "primary", "price": {"eurCents": 7711}, "anyCards": [card],
            "instantBuyCampaign": {"remainingAtCurrentPrice": 2, "remainingSupply": 5, "soldOut": False},
        }]

        rows = _normalize_primary_offers(offers, {"team"}, (0.92, 1.17, 1800))

        self.assertEqual(rows[0]["offer_id"], "primary")
        self.assertEqual(rows[0]["price_eur"], 77.11)
        self.assertEqual(rows[0]["remaining_at_price"], 2)

        offers[0]["instantBuyCampaign"]["remainingSupply"] = 0
        self.assertEqual(_normalize_primary_offers(offers, {"team"}, (0.92, 1.17, 1800)), [])

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

    def test_rows_compare_primary_price_with_secondary_floor_and_sort_by_difference(self):
        listings = [
            {"offer_id": "cheap", "player_slug": "one", "rarity": "rare", "price_eur": 50},
            {"offer_id": "expensive", "player_slug": "two", "rarity": "rare", "price_eur": 1000},
        ]
        histories = {
            (slug, rarity): []
            for slug in ("one", "two")
            for rarity in ("limited", "rare")
        }

        rows, metadata = build_instant_purchase_rows(
            listings,
            histories,
            market_floors={("one", "rare"): 80, ("two", "rare"): 1050},
        )

        self.assertEqual(rows[0]["offer_id"], "expensive")
        self.assertEqual(rows[0]["saving_eur"], 50)
        cheap = next(row for row in rows if row["offer_id"] == "cheap")
        self.assertEqual(cheap["rare_floor"], 80)
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
                "price_eur": 50, "rare_floor": 80, "remaining_at_price": 2, "rare_sales_reference": 75,
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
        InstantPurchaseSnapshot.objects.update(
            rows=[{"offer_id": "old-manager-listing", "team_slug": "other-team"}],
            source_version=1,
        )
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
        snapshot = InstantPurchaseSnapshot.objects.get()
        self.assertEqual(snapshot.source_version, 2)
        self.assertEqual([row["offer_id"] for row in snapshot.rows], ["new"])