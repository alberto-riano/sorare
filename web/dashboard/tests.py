import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase
from django.urls import reverse

import listar_subastas
from dashboard.forms import BatchBidForm, InlineBidForm
from dashboard.models import FavoritePlayer
from dashboard.views import auction_price_history, auctions_list
from web_services.process_runner import BidRequest, ScriptResult, bid_error_message, run_bid_scheduler


class LaLigaAuctionTests(TestCase):
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
        self.assertEqual(rows[0]["player_picture_url"], "https://images.example/player.png")
        self.assertEqual(rows[0]["team_picture_url"], "https://images.example/team.png")
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
            "anyPlayer": {"displayName": "Jugador", "slug": "jugador", "squaredPictureUrl": "https://images.example/player.png"},
            "anyTeam": {"name": "Equipo", "slug": team_slug, "pictureUrl": "https://images.example/team.png"},
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


class AuctionActionsTests(TestCase):
    @patch("dashboard.views.render")
    @patch("listar_subastas.load_auction_cache")
    @patch("listar_subastas.fetch_la_liga_rare_auctions", return_value=[])
    def test_market_sync_metadata_is_shown_in_madrid_time(self, _fetch, load_cache, render):
        load_cache.return_value = {
            "updated_at": "2026-08-12T08:30:00+00:00",
            "last_new_cards_at": "2026-08-12T08:20:00+00:00",
            "new_cards_count": 4,
        }
        render.side_effect = lambda request, template, context: context

        context = auctions_list(RequestFactory().get("/ofertas/"))

        self.assertTrue(context["market_updated_at"].endswith("10:30"))
        self.assertTrue(context["last_new_cards_at"].endswith("10:20"))
        self.assertEqual(context["last_new_cards_count"], 4)

    def test_bid_error_message_keeps_sorare_detail_and_redacts_secrets(self):
        result = ScriptResult(
            "mock", 2, "[10:00:00] Error al pujar (exit code: 2)",
            "❌ Errores preparando la puja:\nLa puja mínima es 12,50 €\nBearer secret-token",
        )
        detail = bid_error_message(result)
        self.assertIn("La puja mínima es 12,50 €", detail)
        self.assertNotIn("secret-token", detail)
        self.assertIn("[oculto]", detail)

    @patch("dashboard.views.run_bid_scheduler")
    @patch("listar_subastas.load_auction_cache")
    def test_batch_bid_failure_shows_player_and_real_description(self, load_cache, run_bid):
        user = get_user_model().objects.create_user(username="bid-error-user")
        self.client.force_login(user)
        load_cache.return_value = {"auctions": [{"auction_id": "EnglishAuction:test", "player": "Oyarzabal"}]}
        run_bid.return_value = ScriptResult("mock", 2, "", "❌ La puja mínima es 15,00 €")
        bids = json.dumps([{"auction_id": "EnglishAuction:test", "euros": "12.50", "use_credit": True}])

        response = self.client.post(f'{reverse("auctions_list")}?page=5&team=Real+Sociedad', {
            "action": "place_batch_bids", "bids": bids, "confirm": "on",
        })

        rendered_messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f'{reverse("auctions_list")}?page=5&team=Real+Sociedad')
        self.assertIn("Oyarzabal: La puja mínima es 15,00 €", rendered_messages)
        self.assertFalse(any("posiciones" in message for message in rendered_messages))

    def test_favorite_toggle_is_persisted_per_user(self):
        user = get_user_model().objects.create_user(username="favorites-user", password="test-password")
        self.client.force_login(user)
        url = reverse("toggle_favorite_player")

        added = self.client.post(url, {"player_slug": "jugador-prueba", "player_name": "Jugador Prueba"})
        self.assertEqual(added.status_code, 200)
        self.assertTrue(added.json()["favorite"])
        self.assertTrue(FavoritePlayer.objects.filter(user=user, player_slug="jugador-prueba").exists())

        removed = self.client.post(url, {"player_slug": "jugador-prueba", "player_name": "Jugador Prueba"})
        self.assertFalse(removed.json()["favorite"])
        self.assertFalse(FavoritePlayer.objects.filter(user=user, player_slug="jugador-prueba").exists())

    def test_batch_bid_requires_final_confirmation_and_validates_every_bid(self):
        bids = json.dumps([
            {"auction_id": "EnglishAuction:first", "euros": "12.50", "use_credit": True},
            {"auction_id": "EnglishAuction:second", "euros": "8.25", "use_credit": False},
        ])
        unconfirmed = BatchBidForm({"bids": bids})
        self.assertFalse(unconfirmed.is_valid())
        self.assertIn("confirm", unconfirmed.errors)

        confirmed = BatchBidForm({"bids": bids, "confirm": "on"})
        self.assertTrue(confirmed.is_valid())
        self.assertEqual(len(confirmed.cleaned_data["bids"]), 2)
        self.assertTrue(confirmed.cleaned_data["bids"][0]["use_credit"])
        self.assertFalse(confirmed.cleaned_data["bids"][1]["use_credit"])

    @patch("dashboard.views.messages.success")
    @patch("dashboard.views.render")
    @patch("listar_subastas.fetch_la_liga_rare_auctions", return_value=[])
    @patch("listar_subastas.refresh_auction_cache")
    def test_refresh_button_forces_a_full_market_scan(self, refresh_cache, fetch_auctions, render, success):
        render.side_effect = lambda request, template, context: context
        auctions_list(RequestFactory().post("/ofertas/", {"action": "refresh_market"}))
        refresh_cache.assert_called_once_with(force_full=True)

    @patch("dashboard.views.render")
    @patch("listar_subastas.fetch_la_liga_rare_auctions")
    def test_auctions_are_sorted_paginated_and_shown_in_madrid_time(self, fetch_auctions, render):
        fetch_auctions.return_value = [
            {
                "player": f"Jugador {index}", "team": "Equipo", "position": "Forward",
                "player_slug": f"jugador-{index}",
                "bid_eur": 10, "is_winning": False, "is_outbid": False, "has_bid": index == 23,
                "end_date": f"2026-08-12T{hour:02d}:00:00Z",
            }
            for index, hour in enumerate(list(range(23, -1, -1)) + [23])
        ]
        render.side_effect = lambda request, template, context: context
        context = auctions_list(RequestFactory().get("/ofertas/"))

        self.assertEqual(len(context["auctions"]), 20)
        self.assertEqual(context["page_obj"].paginator.per_page, 20)
        self.assertEqual(context["auctions"][0]["end_date"], "2026-08-12T00:00:00Z")
        end_at = datetime.fromisoformat("2026-08-12T00:00:00+00:00").astimezone(ZoneInfo("Europe/Madrid"))
        days_until = (end_at.date() - datetime.now(ZoneInfo("Europe/Madrid")).date()).days
        expected_end = f"Hoy {end_at:%H:%M}" if days_until == 0 else f"Mañana {end_at:%H:%M}" if days_until == 1 else end_at.strftime("%d/%m/%Y %H:%M")
        self.assertEqual(context["auctions"][0]["end_date_madrid"], expected_end)

        descending = auctions_list(RequestFactory().get("/ofertas/?end_order=desc&page=2"))
        self.assertEqual(descending["end_order"], "desc")
        self.assertEqual(descending["page_obj"].number, 2)
        self.assertEqual(descending["auctions"][0]["end_date"], "2026-08-12T04:00:00Z")

        user = get_user_model().objects.create_user(username="filter-user")
        FavoritePlayer.objects.create(user=user, player_slug="jugador-23", player_name="Jugador 23")
        favorite_request = RequestFactory().get("/ofertas/?favorites=1")
        favorite_request.user = user
        favorites = auctions_list(favorite_request)
        self.assertEqual(favorites["filtered_count"], 1)
        self.assertEqual(favorites["auctions"][0]["player_slug"], "jugador-23")

        participated = auctions_list(RequestFactory().get("/ofertas/?has_bid=1"))
        self.assertEqual(participated["filtered_count"], 1)
        self.assertEqual(participated["auctions"][0]["player_slug"], "jugador-23")

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
