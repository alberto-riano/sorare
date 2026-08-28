from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from web_services.config_files import SorarePaths
from web_services.direct_offer_market import player_in_season_listings
from web_services.process_runner import ScriptResult, run_direct_offer


class DirectOfferMarketTests(TestCase):
    @patch("web_services.direct_offer_market.get_live_single_sale_offers")
    def test_listings_include_manager_and_only_in_season_cards(self, get_offers):
        get_offers.return_value = [
            {
                "id": "offer-in-season",
                "sender": {"slug": "seller-one", "nickname": "Seller One"},
                "senderSide": {"anyCards": [{
                    "assetId": "asset-one", "slug": "card-one", "name": "Card One",
                    "rarityTyped": "rare", "seasonYear": 2026, "serialNumber": 12,
                    "inSeasonEligible": True, "anyPlayer": {"displayName": "Jugador Uno"},
                    "anyTeam": {"name": "Equipo Uno"},
                }]},
                "receiverSide": {"amounts": {"eurCents": 1234}},
            },
            {
                "id": "offer-classic",
                "sender": {"slug": "seller-two", "nickname": "Seller Two"},
                "senderSide": {"anyCards": [{
                    "assetId": "asset-two", "rarityTyped": "rare",
                    "inSeasonEligible": False,
                }]},
                "receiverSide": {"amounts": {"eurCents": 900}},
            },
        ]

        rows = player_in_season_listings("jugador-uno", headers={"Authorization": "hidden"})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset_id"], "asset-one")
        self.assertEqual(rows[0]["manager"], "Seller One")
        self.assertEqual(rows[0]["price_eur"], Decimal("12.34"))


class DirectOfferViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="direct-offer-user")
        self.client.force_login(user)
        self.listing = {
            "asset_id": "asset-one", "manager_slug": "seller-one", "manager": "Seller One",
            "rarity": "rare", "price_eur": Decimal("12.34"),
        }

    @patch("dashboard.direct_offer_views.search_players_by_name")
    @patch("dashboard.direct_offer_views.build_headers", return_value={})
    def test_player_search_returns_matches(self, _headers, search_players):
        search_players.return_value = [{"slug": "jugador-uno", "displayName": "Jugador Uno"}]

        response = self.client.get(reverse("direct_offer_player_search"), {"q": "Juga"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["players"][0]["slug"], "jugador-uno")

    @patch("dashboard.direct_offer_views.run_direct_offer")
    @patch("dashboard.direct_offer_views.player_in_season_listings")
    @patch("dashboard.direct_offer_views.build_headers", return_value={})
    def test_create_offer_revalidates_listing_and_runs_once(self, _headers, listings, run_offer):
        listings.return_value = [self.listing]
        run_offer.return_value = ScriptResult("node", 0, "ok", "")

        response = self.client.post(
            reverse("create_direct_offer"),
            data={
                "player_slug": "jugador-uno", "asset_id": "asset-one",
                "manager_slug": "seller-one", "euros": "10.25", "duration_hours": 48,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        run_offer.assert_called_once_with(
            unittest_mock_any_paths(), asset_id="asset-one", manager_slug="seller-one",
            euros="10.25", duration_hours=48,
        )

    @patch("dashboard.direct_offer_views.run_direct_offer")
    @patch("dashboard.direct_offer_views.player_in_season_listings", return_value=[])
    @patch("dashboard.direct_offer_views.build_headers", return_value={})
    def test_create_offer_rejects_stale_listing_without_running_node(self, _headers, _listings, run_offer):
        response = self.client.post(
            reverse("create_direct_offer"),
            data={
                "player_slug": "jugador-uno", "asset_id": "asset-one",
                "manager_slug": "seller-one", "euros": "10.25", "duration_hours": 48,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        run_offer.assert_not_called()


class DirectOfferRunnerTests(TestCase):
    @patch("web_services.process_runner._run_command")
    def test_runner_builds_direct_offer_command(self, run_command):
        run_command.return_value = ScriptResult("node", 0, "", "")
        paths = SorarePaths(repo_root=Path("/repo"))

        run_direct_offer(
            paths, asset_id=" asset-one ", manager_slug=" seller-one ",
            euros="10.25", duration_hours=72,
        )

        self.assertEqual(run_command.call_args.args[0], [
            "node", "/repo/javascript/vender_carta.js", "--direct-offer",
            "asset-one", "seller-one", "1025", "72",
        ])


def unittest_mock_any_paths():
    from unittest.mock import ANY
    return ANY
