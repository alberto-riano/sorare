import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

import listar_subastas
from dashboard.forms import InlineBidForm
from dashboard.views import auction_price_history, auctions_list
from web_services.process_runner import BidRequest, ScriptResult, run_bid_scheduler


class LaLigaAuctionTests(SimpleTestCase):
    def test_fetch_la_liga_teams_uses_season_contestants(self):
        response = {
            "football": {
                "competition": {
                    "displayName": "LALIGA EA Sports",
                    "contestants": [
                        {"anyTeam": {"name": "Real Madrid", "slug": "real-madrid-madrid"}},
                        {"anyTeam": {"name": "FC Barcelona", "slug": "barcelona-barcelona"}},
                    ],
                }
            }
        }
        with patch.object(listar_subastas, "graphql_request", return_value=response) as request:
            teams = listar_subastas.fetch_la_liga_teams({}, season_year=2026)

        self.assertEqual(set(teams), {"real-madrid-madrid", "barcelona-barcelona"})
        self.assertEqual(request.call_args.args[1]["seasonYear"], 2026)

    def test_live_auctions_filters_rarity_season_and_laliga_team(self):
        cards = [
            self._card("wanted", "rare", 2026, "real-madrid-madrid"),
            self._card("old-season", "rare", 2025, "real-madrid-madrid"),
            self._card("wrong-rarity", "limited", 2026, "real-madrid-madrid"),
            self._card("wrong-league", "rare", 2026, "arsenal-london"),
        ]
        response = {"currentUser": {"nickname": "burguis", "buyingTokenAuctions": [self._auction(card) for card in cards]}}
        response["currentUser"]["buyingTokenAuctions"][0]["bestBid"] = {
            "amounts": {"eurCents": 1250},
            "userBidder": {"nickname": "BURGuis"},
        }
        with patch.object(listar_subastas, "graphql_request", return_value=response):
            auctions, pages, total = listar_subastas.fetch_all_live_auctions(
                {}, rarity="rare", team_slugs={"real-madrid-madrid"}, season_year=2026
            )

        self.assertEqual([auction["asset_id"] for auction in auctions], ["wanted"])
        self.assertTrue(auctions[0]["is_winning"])
        self.assertEqual((pages, total), (1, 4))

    def test_bid_position_keeps_real_winner_first(self):
        response = {
            "tokens": {
                "a0": {
                    "bestBid": {"userBidder": {"nickname": "winner"}},
                    "bids": {
                        "nodes": [
                            {"maximumAmounts": {"eurCents": 5000}, "amounts": {"eurCents": 5000}, "userBidder": {"nickname": "other"}},
                            {"maximumAmounts": {"eurCents": 3000}, "amounts": {"eurCents": 2990}, "userBidder": {"nickname": "burguis"}},
                            {"maximumAmounts": {"eurCents": 4000}, "amounts": {"eurCents": 4000}, "userBidder": {"nickname": "winner"}},
                        ]
                    },
                }
            }
        }
        with patch.object(listar_subastas, "graphql_request", return_value=response):
            positions = listar_subastas.fetch_bid_positions(
                {}, [{"auction_id": "EnglishAuction:test"}], "BURGuis"
            )
        self.assertEqual(positions["EnglishAuction:test"], 3)

    def test_market_rows_include_auctions_without_my_bid(self):
        card = self._card("market-card", "rare", 2026, "real-madrid-madrid")
        auction = self._auction(card)
        rows = listar_subastas._rows_from_live_auctions(
            [auction], {"real-madrid-madrid"}, "burguis", season_year=2026
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["has_bid"])
        self.assertFalse(rows[0]["is_winning"])
        self.assertFalse(rows[0]["is_outbid"])

    @staticmethod
    def _card(asset_id, rarity, season, team_slug):
        card = {
            "assetId": asset_id,
            "rarityTyped": rarity,
            "seasonYear": season,
            "serialNumber": 1,
            "anyPlayer": {"displayName": "Jugador", "slug": "jugador"},
            "anyTeam": {"name": "Equipo", "slug": team_slug},
            "anyPositions": ["Defender"],
        }
        return card

    @staticmethod
    def _auction(card):
        return {
            "id": f"EnglishAuction:{card['assetId']}",
            "currentPrice": "0",
            "endDate": "2026-08-12T12:00:00Z",
            "open": True,
            "bestBid": None,
            "anyCards": [card],
        }


class AuctionActionsTests(SimpleTestCase):
    @patch("dashboard.views.render")
    @patch("listar_subastas.fetch_la_liga_rare_auctions")
    def test_auctions_are_sorted_paginated_and_shown_in_madrid_time(self, fetch_auctions, render):
        fetch_auctions.return_value = [
            {
                "player": f"Jugador {index}", "team": "Equipo", "position": "Forward",
                "bid_eur": 10, "is_winning": False, "is_outbid": False,
                "end_date": f"2026-08-12T{hour:02d}:00:00Z",
            }
            for index, hour in enumerate(list(range(23, -1, -1)) + [23])
        ]
        render.side_effect = lambda request, template, context: context
        context = auctions_list(RequestFactory().get("/ofertas/"))

        self.assertEqual(len(context["auctions"]), 20)
        self.assertEqual(context["page_obj"].paginator.per_page, 20)
        self.assertEqual(context["auctions"][0]["end_date"], "2026-08-12T00:00:00Z")
        self.assertEqual(context["auctions"][0]["end_date_madrid"], "12/08/2026 02:00")

        descending = auctions_list(RequestFactory().get("/ofertas/?end_order=desc&page=2"))
        self.assertEqual(descending["end_order"], "desc")
        self.assertEqual(descending["page_obj"].number, 2)
        self.assertEqual(descending["auctions"][0]["end_date"], "2026-08-12T04:00:00Z")

    def test_inline_bid_requires_explicit_confirmation(self):
        form = InlineBidForm({"auction_id": "EnglishAuction:test", "euros": "12.50"})
        self.assertFalse(form.is_valid())
        self.assertIn("confirm", form.errors)

    @patch("web_services.process_runner._run_command")
    def test_inline_bid_builds_now_command_without_executing_real_bid(self, run_command):
        run_command.return_value = ScriptResult("mock", 0, "", "")
        run_bid_scheduler(
            self._paths(),
            BidRequest("EnglishAuction:test", "12.50", "", True, False, False, True),
        )
        command = run_command.call_args.args[0]
        self.assertIn("--now", command)
        self.assertIn("--use-credit", command)
        self.assertEqual(command[2:4], ["EnglishAuction:test", "12.50"])

    @patch("sorare_utils.build_headers", return_value={})
    @patch("sorare_utils.get_recent_prices")
    def test_history_classifies_sale_types(self, get_prices, _headers):
        get_prices.return_value = [
            {"amounts": {"eurCents": 1234}, "date": "2026-08-01T10:00:00Z", "deal": {"__typename": "TokenAuction"}},
            {"amounts": {"eurCents": 1500}, "date": "2026-08-02T10:00:00Z", "deal": {"__typename": "TokenPrimaryOffer"}},
            {
                "amounts": {"eurCents": 900},
                "date": "2026-08-03T10:00:00Z",
                "deal": {
                    "__typename": "TokenOffer",
                    "type": "DIRECT_OFFER",
                    "senderSide": {"anyCards": [{"assetId": "a"}]},
                    "receiverSide": {"anyCards": [{"assetId": "b"}]},
                },
            },
        ]
        response = auction_price_history(RequestFactory().get("/", {"player_slug": "test-player"}))
        payload = json.loads(response.content)
        self.assertEqual([sale["kind"] for sale in payload["sales"]], ["auction", "instant", "trade"])

    @staticmethod
    def _paths():
        from pathlib import Path
        from web_services.config_files import SorarePaths

        return SorarePaths(repo_root=Path(__file__).resolve().parents[2])
