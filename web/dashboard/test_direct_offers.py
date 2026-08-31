from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from web_services.config_files import SorarePaths
from web_services.direct_offer_market import (
    check_direct_offer_eur,
    direct_offer_payment_amount,
    player_in_season_listings,
)
from web_services.process_runner import ScriptResult, run_direct_offer


class DirectOfferMarketTests(TestCase):
    @patch("web_services.direct_offer_market.graphql_request")
    def test_eth_payment_converts_eur_reference_to_wei(self, graphql):
        graphql.return_value = {"config": {"exchangeRate": {"ethRates": {"eurCents": 200000}}}}

        amount = direct_offer_payment_amount(2403, "ETH", headers={"Authorization": "hidden"})

        self.assertEqual(amount, {"amount": "12015000000000000", "currency": "WEI"})

    @patch("web_services.direct_offer_market.graphql_request")
    def test_eur_preflight_uses_eur_settlement_without_creating_offer(self, graphql):
        graphql.return_value = {"prepareOffer": {"errors": []}}

        errors = check_direct_offer_eur(
            "asset-one", "seller-one", 22, headers={"Authorization": "hidden"},
        )

        self.assertEqual(errors, [])
        query, variables = graphql.call_args.args[:2]
        self.assertIn("prepareOffer", query)
        self.assertNotIn("createDirectOffer", query)
        self.assertEqual(variables["input"]["settlementCurrencies"], ["EUR"])
        self.assertEqual(variables["input"]["sendAmount"], {"amount": "22", "currency": "EUR"})

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
            {
                "id": "offer-in-eth",
                "sender": {"slug": "seller-three", "nickname": "Seller Three"},
                "senderSide": {"anyCards": [{
                    "assetId": "asset-three", "slug": "card-three", "name": "Card Three",
                    "rarityTyped": "limited", "seasonYear": 2026, "serialNumber": 48,
                    "inSeasonEligible": True, "anyPlayer": {"displayName": "Jugador Uno"},
                    "anyTeam": {"name": "Equipo Uno"},
                }]},
                "receiverSide": {"amounts": {"eurCents": 0, "wei": "200000000000000"}},
            },
        ]

        rows = player_in_season_listings(
            "jugador-uno",
            headers={"Authorization": "hidden"},
            rates=(0.92, 1.17, 2150),
        )

        self.assertEqual(len(rows), 2)
        eth_row = next(row for row in rows if row["asset_id"] == "asset-three")
        self.assertEqual(eth_row["manager"], "Seller Three")
        self.assertEqual(eth_row["price_eur"], Decimal("0.43"))
        self.assertEqual(eth_row["price_currency"], "ETH")
        self.assertEqual(eth_row["price_original"], Decimal("0.0002"))
        eur_row = next(row for row in rows if row["asset_id"] == "asset-one")
        self.assertEqual(eur_row["price_eur"], Decimal("12.34"))
        self.assertEqual(eur_row["price_currency"], "EUR")


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

    @patch("dashboard.direct_offer_views.direct_offer_payment_amount")
    @patch("dashboard.direct_offer_views.check_direct_offer_payment")
    @patch("dashboard.direct_offer_views.player_in_season_listings")
    @patch("dashboard.direct_offer_views.build_headers", return_value={})
    def test_preview_reports_eur_compatibility_and_stale_sales(self, _headers, listings, check_payment, payment_amount):
        listings.return_value = [self.listing, {
            **self.listing, "asset_id": "asset-two", "manager_slug": "seller-two",
            "manager": "Seller Two", "serial_number": 22,
        }]
        payment_amount.return_value = {"amount": "22", "currency": "EUR"}
        check_payment.side_effect = [[], ["El manager no puede recibir EUR."]]

        response = self.client.post(
            reverse("preview_direct_offers"),
            data={
                "player_slug": "jugador-uno", "euros": "0.22",
                "offers": [
                    {"asset_id": "asset-one", "manager_slug": "seller-one"},
                    {"asset_id": "asset-two", "manager_slug": "seller-two"},
                    {"asset_id": "stale", "manager_slug": "seller-stale"},
                ],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["currency"], "EUR")
        self.assertEqual(payload["compatible_count"], 1)
        self.assertTrue(payload["results"][0]["compatible"])
        self.assertFalse(payload["results"][1]["compatible"])
        self.assertIn("no puede recibir EUR", payload["results"][1]["error"])
        self.assertIn("ya no está disponible", payload["results"][2]["error"])
        self.assertEqual(check_payment.call_count, 2)

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
                "manager_slug": "seller-one", "euros": "0.22", "duration_hours": 48,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        run_offer.assert_called_once_with(
            unittest_mock_any_paths(), asset_id="asset-one", manager_slug="seller-one",
            euros="0.22", duration_hours=48, currency="EUR",
        )

    @patch("dashboard.direct_offer_views.run_direct_offer")
    @patch("dashboard.direct_offer_views.player_in_season_listings")
    @patch("dashboard.direct_offer_views.build_headers", return_value={})
    def test_create_offer_preserves_eth_payment_choice(self, _headers, listings, run_offer):
        listings.return_value = [self.listing]
        run_offer.return_value = ScriptResult("node", 0, "ok", "")

        response = self.client.post(
            reverse("create_direct_offer"),
            data={
                "player_slug": "jugador-uno", "asset_id": "asset-one",
                "manager_slug": "seller-one", "euros": "24.03",
                "currency": "ETH", "duration_hours": 48,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        run_offer.assert_called_once_with(
            unittest_mock_any_paths(), asset_id="asset-one", manager_slug="seller-one",
            euros="24.03", duration_hours=48, currency="ETH",
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
            "asset-one", "seller-one", "1025", "72", "--currency", "EUR",
        ])

        run_direct_offer(
            paths, asset_id="asset-two", manager_slug="seller-two",
            euros="24.03", duration_hours=48, currency="ETH",
        )
        self.assertEqual(run_command.call_args.args[0][-2:], ["--currency", "ETH"])


def unittest_mock_any_paths():
    from unittest.mock import ANY
    return ANY
