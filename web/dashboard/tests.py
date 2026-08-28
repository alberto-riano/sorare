import json
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase
from django.urls import reverse

import listar_subastas
import sorare_utils
from dashboard.forms import BatchBidForm, BatchSaleForm, InlineBidForm
from dashboard.management.commands.process_bid_queue import process_next_job
from dashboard.management.commands.process_sales_queue import (
    process_next_auction_refresh, process_next_movement_sync, process_next_public_reward_sync,
    process_next_opportunity_refresh, process_next_refresh, process_next_sale,
)
from dashboard.models import (
    AuctionFilterPreset, AuctionRefreshJob, BidBatchItem, BidBatchJob, FavoritePlayer,
    MovementSnapshot, MovementSyncJob, PublicRewardSnapshot, PublicRewardSyncJob,
    OpportunityRefreshJob, OpportunitySnapshot, SaleBatchItem, SaleBatchJob,
    SalesInventory, SalesRefreshJob,
)
from dashboard.views import auction_price_history, auctions_list
from sorare_utils import get_latest_prices
from web_services.process_runner import BidRequest, ScriptResult, bid_error_message, run_bid_scheduler, run_card_sale
from web_services.sales_inventory import collection_display_name
from web_services.movement_history import (
    _account_currency, _movement_from_group, _operation_from_trade, build_trade_cycles,
    collect_movement_history, collect_public_reward_history,
)
from web_services.opportunity_market import (
    _comparable_kind, _floor_query, _history_query, build_opportunity_rows, robust_sales_reference,
)


class LaLigaAuctionTests(TestCase):
    @patch("sorare_utils.time.sleep")
    @patch("sorare_utils.requests.post")
    def test_graphql_request_respects_rate_limit_retry_after(self, post, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "2"})
        success = Mock(status_code=200)
        success.json.return_value = {"data": {"ok": True}}
        post.side_effect = [limited, success]

        result = sorare_utils.graphql_request("query { ok }", headers={})

        self.assertEqual(result, {"ok": True})
        sleep.assert_called_once_with(2.0)
        success.raise_for_status.assert_called_once()

    @patch.object(listar_subastas, "fetch_bid_positions", return_value={})
    @patch.object(listar_subastas, "fetch_la_liga_teams", return_value={"real-madrid-madrid": "Real Madrid"})
    @patch.object(listar_subastas, "build_headers", return_value={})
    def test_cache_refresh_always_rebuilds_full_public_feed(self, _headers, _teams, _positions):
        card = self._card("new-market-card", "rare", 2026, "real-madrid-madrid")
        auction = self._auction(card)
        auction["endDate"] = "2099-08-20T12:00:00Z"
        response = {
            "currentUser": {"nickname": "burguis"},
            "tokens": {"liveAuctions": {
                "totalCount": 1, "nodes": [auction],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }},
        }
        old_cache = {"updated_at": "2026-08-13T00:00:00+00:00", "auctions": [{"auction_id": "EnglishAuction:old", "asset_id": "old"}]}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(listar_subastas, "CACHE_PATH", Path(directory) / "market.json"), \
             patch.object(listar_subastas, "load_auction_cache", return_value=old_cache), \
             patch.object(listar_subastas, "graphql_request", return_value=response) as request:
            payload = listar_subastas.refresh_auction_cache()

        self.assertEqual([row["asset_id"] for row in payload["auctions"]], ["new-market-card"])
        self.assertIsNone(request.call_args.args[1]["updatedAfter"])

    @patch.object(listar_subastas, "fetch_bid_positions", return_value={"EnglishAuction:known": 2})
    @patch.object(listar_subastas, "build_headers", return_value={})
    def test_quick_refresh_only_updates_known_auctions(self, _headers, _positions):
        old_cache = {
            "updated_at": "2026-08-13T00:00:00+00:00",
            "full_refreshed_at": "2026-08-13T00:00:00+00:00",
            "my_nickname": "burguis",
            "new_cards_count": 3,
            "auctions": [{
                "auction_id": "EnglishAuction:known", "asset_id": "known",
                "player": "Jugador", "end_date": "2099-08-20T12:00:00Z",
                "bid_eur": 10, "has_bid": False, "is_winning": False, "is_outbid": False,
            }],
        }
        response = {
            "currentUser": {"nickname": "burguis"},
            "tokens": {"a0": {
                "id": "EnglishAuction:known", "open": True, "endDate": "2099-08-20T12:00:00Z",
                "bestBid": {"amounts": {"eurCents": 1350}, "userBidder": {"nickname": "otro"}},
                "myLastBid": {"amounts": {"eurCents": 1200}, "maximumAmounts": {"eurCents": 1300}},
            }},
        }
        progress = []
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(listar_subastas, "CACHE_PATH", Path(directory) / "market.json"), \
             patch.object(listar_subastas, "graphql_request", return_value=response):
            listar_subastas.CACHE_PATH.write_text(json.dumps(old_cache), encoding="utf-8")
            payload = listar_subastas.refresh_cached_auction_prices(
                progress=lambda processed, total, label: progress.append((processed, total, label)),
            )

        row = payload["auctions"][0]
        self.assertEqual(row["bid_eur"], 13.5)
        self.assertEqual(row["my_bid_eur"], 13.0)
        self.assertTrue(row["is_outbid"])
        self.assertEqual(row["bid_position"], 2)
        self.assertEqual(payload["full_refreshed_at"], "2026-08-13T00:00:00+00:00")
        self.assertEqual(payload["last_refresh_mode"], "quick")
        self.assertEqual(progress[-1][:2], (1, 1))

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

    def test_winning_market_row_keeps_my_maximum_bid(self):
        card = self._card("winning-card", "rare", 2026, "real-madrid-madrid")
        auction = self._auction(card)
        auction["bestBid"] = {
            "amounts": {"eurCents": 1250},
            "userBidder": {"nickname": "BURGuis"},
        }
        auction["myLastBid"] = {
            "amounts": {"eurCents": 1250},
            "maximumAmounts": {"eurCents": 2500},
        }
        rows = listar_subastas._rows_from_live_auctions(
            [auction], {"real-madrid-madrid"}, "burguis", season_year=2026
        )
        self.assertTrue(rows[0]["is_winning"])
        self.assertEqual(rows[0]["my_bid_eur"], 25.0)

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
    @patch("listar_subastas.fetch_la_liga_rare_auctions", return_value=[])
    def test_auction_page_uses_compact_controls(self, _fetch):
        user = get_user_model().objects.create_user(username="compact-auctions")
        self.client.force_login(user)
        response = self.client.get(reverse("auctions_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guardar filtro")
        self.assertNotContains(response, "Buscar jugador")
        self.assertNotContains(response, "Por página")
        self.assertNotContains(response, "Últimas 5")
        self.assertNotContains(response, "Páginas de subastas")

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
    def test_batch_bid_is_queued_without_running_in_web_request(self, load_cache, run_bid):
        user = get_user_model().objects.create_user(username="bid-error-user")
        self.client.force_login(user)
        load_cache.return_value = {"auctions": [{"auction_id": "EnglishAuction:test", "player": "Oyarzabal"}]}
        bids = json.dumps([{"auction_id": "EnglishAuction:test", "euros": "12.50", "use_credit": True, "currency": "ETH"}])

        response = self.client.post(reverse("enqueue_batch_bids"), {
            "bids": bids, "confirm": "on", "request_key": "39ed4b7a-28cd-44d4-8e45-a009ac9db384",
        })

        self.assertEqual(response.status_code, 202)
        run_bid.assert_not_called()
        job = BidBatchJob.objects.get(id=response.json()["job_id"])
        self.assertEqual(job.status, BidBatchJob.Status.QUEUED)
        self.assertEqual(job.items.get().player_name, "Oyarzabal")
        self.assertEqual(job.items.get().currency, "ETH")

    @patch("dashboard.management.commands.process_bid_queue.run_bid_scheduler")
    def test_worker_preserves_detailed_bid_failure(self, run_bid):
        user = get_user_model().objects.create_user(username="worker-user")
        job = BidBatchJob.objects.create(user=user, total_count=1)
        BidBatchItem.objects.create(
            job=job, position=1, auction_id="EnglishAuction:test",
            player_name="Oyarzabal", euros="12.50", currency="ETH",
        )
        run_bid.return_value = ScriptResult("mock", 2, "", "❌ La puja mínima es 15,00 €")

        process_next_job()

        job.refresh_from_db()
        item = job.items.get()
        self.assertEqual(job.status, BidBatchJob.Status.FAILED)
        self.assertEqual(item.error, "La puja mínima es 15,00 €")
        self.assertEqual(run_bid.call_args.args[1].currency, "ETH")

    def test_bid_status_does_not_expose_another_users_jobs(self):
        owner = get_user_model().objects.create_user(username="job-owner")
        viewer = get_user_model().objects.create_user(username="job-viewer")
        job = BidBatchJob.objects.create(user=owner, total_count=1)
        self.client.force_login(viewer)
        response = self.client.get(reverse("bid_jobs_status"), {"ids": str(job.id)})
        self.assertEqual(response.json()["jobs"], [])

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

    def test_saved_filter_is_private_and_keeps_multiple_teams(self):
        user = get_user_model().objects.create_user(username="preset-user")
        self.client.force_login(user)
        query = "teams=Real+Madrid&teams=FC+Barcelona&positions=Defender&positions=Forward&favorites=1&per_page=50&page=8"
        response = self.client.post(reverse("save_auction_filter"), {"name": "Grandes", "query": query})
        preset = AuctionFilterPreset.objects.get(user=user, name="Grandes")
        self.assertEqual(response.status_code, 302)
        self.assertIn("teams=Real+Madrid", preset.query_string)
        self.assertIn("teams=FC+Barcelona", preset.query_string)
        self.assertIn("positions=Defender", preset.query_string)
        self.assertIn("positions=Forward", preset.query_string)
        self.assertNotIn("page=8", preset.query_string)

    def test_batch_bid_requires_final_confirmation_and_validates_every_bid(self):
        bids = json.dumps([
            {"auction_id": "EnglishAuction:first", "euros": "12.50", "use_credit": True, "currency": "EUR"},
            {"auction_id": "EnglishAuction:second", "euros": "8.25", "use_credit": False, "currency": "ETH"},
        ])
        unconfirmed = BatchBidForm({"bids": bids})
        self.assertFalse(unconfirmed.is_valid())
        self.assertIn("confirm", unconfirmed.errors)

        confirmed = BatchBidForm({"bids": bids, "confirm": "on"})
        self.assertTrue(confirmed.is_valid())
        self.assertEqual(len(confirmed.cleaned_data["bids"]), 2)
        self.assertTrue(confirmed.cleaned_data["bids"][0]["use_credit"])
        self.assertFalse(confirmed.cleaned_data["bids"][1]["use_credit"])
        self.assertEqual(confirmed.cleaned_data["bids"][1]["currency"], "ETH")

    @patch("listar_subastas.load_auction_cache", return_value={"auctions": []})
    def test_market_refresh_is_queued_and_exposes_progress(self, _cache):
        user = get_user_model().objects.create_user(username="market-refresh-user")
        self.client.force_login(user)
        response = self.client.post(reverse("enqueue_auction_refresh"), {"mode": "quick"})

        self.assertEqual(response.status_code, 202)
        job = AuctionRefreshJob.objects.get(pk=response.json()["job_id"])
        self.assertEqual(job.mode, AuctionRefreshJob.Mode.QUICK)
        job.status = AuctionRefreshJob.Status.RUNNING
        job.processed_count = 25
        job.total_count = 100
        job.progress_label = "Pujas revisadas: 25/100"
        job.save()

        status = self.client.get(reverse("auction_refresh_status"), {"id": job.id}).json()["job"]
        self.assertEqual(status["percent"], 25)
        self.assertEqual(status["progress_label"], "Pujas revisadas: 25/100")

    @patch("listar_subastas.refresh_cached_auction_prices")
    def test_market_worker_processes_quick_refresh(self, refresh_prices):
        user = get_user_model().objects.create_user(username="market-worker-user")
        job = AuctionRefreshJob.objects.create(user=user, mode=AuctionRefreshJob.Mode.QUICK)

        def complete(progress):
            progress(4, 4, "Pujas revisadas: 4/4")
            return {"auctions": [{}, {}, {}], "new_cards_count": 0}

        refresh_prices.side_effect = complete
        process_next_auction_refresh()

        job.refresh_from_db()
        self.assertEqual(job.status, AuctionRefreshJob.Status.SUCCEEDED)
        self.assertEqual(job.processed_count, 4)
        self.assertEqual(job.total_count, 4)
        self.assertEqual(job.auction_count, 3)

    @patch("dashboard.views.render")
    @patch("listar_subastas.fetch_la_liga_rare_auctions")
    def test_auctions_show_up_to_one_hundred_and_use_madrid_time(self, fetch_auctions, render):
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

        self.assertEqual(len(context["auctions"]), 25)
        self.assertEqual(context["page_obj"].paginator.per_page, 100)
        self.assertEqual(context["auctions"][0]["end_date"], "2026-08-12T00:00:00Z")
        end_at = datetime.fromisoformat("2026-08-12T00:00:00+00:00").astimezone(ZoneInfo("Europe/Madrid"))
        days_until = (end_at.date() - datetime.now(ZoneInfo("Europe/Madrid")).date()).days
        expected_end = f"Hoy {end_at:%H:%M}" if days_until == 0 else f"Mañana {end_at:%H:%M}" if days_until == 1 else end_at.strftime("%d/%m/%Y %H:%M")
        self.assertEqual(context["auctions"][0]["end_date_madrid"], expected_end)

        descending = auctions_list(RequestFactory().get("/ofertas/?end_order=desc"))
        self.assertEqual(descending["end_order"], "desc")
        self.assertEqual(descending["page_obj"].number, 1)
        self.assertEqual(descending["auctions"][0]["end_date"], "2026-08-12T23:00:00Z")

        fifty_per_page = auctions_list(RequestFactory().get("/ofertas/?per_page=50"))
        self.assertEqual(fifty_per_page["page_obj"].paginator.per_page, 100)
        self.assertEqual(len(fifty_per_page["auctions"]), 25)

        multiple_values = auctions_list(RequestFactory().get("/ofertas/?teams=Equipo&teams=Otro&positions=Forward&positions=Goalkeeper"))
        self.assertEqual(multiple_values["filter_teams"], ["Equipo", "Otro"])
        self.assertEqual(multiple_values["filter_positions"], ["Forward", "Goalkeeper"])
        self.assertEqual(multiple_values["filtered_count"], 25)

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
        self.assertEqual(command[-2:], ["--currency", "EUR"])
        self.assertEqual(command[2:4], ["EnglishAuction:test", "12.50"])

        run_bid_scheduler(
            self._paths(),
            BidRequest("EnglishAuction:test", "12.50", "", True, False, False, False, "ETH"),
        )
        eth_command = run_command.call_args.args[0]
        self.assertNotIn("--use-credit", eth_command)
        self.assertEqual(eth_command[-2:], ["--currency", "ETH"])

    @patch("sorare_utils.build_headers", return_value={})
    @patch("sorare_utils.get_recent_prices")
    def test_history_classifies_sale_types(self, get_prices, _headers):
        get_prices.return_value = [
            {"amounts": {"eurCents": 1234}, "date": "2026-08-01T10:00:00Z", "deal": {"__typename": "TokenAuction"}},
            {"amounts": {"eurCents": 1500}, "date": "2026-08-02T10:00:00Z", "deal": {"__typename": "TokenPrimaryOffer"}},
            {
                "amounts": {"eurCents": 361},
                "date": "2026-08-02T11:00:00Z",
                "deal": {
                    "__typename": "TokenOffer", "type": "SINGLE_BUY_OFFER",
                    "senderSide": {"anyCards": []},
                    "receiverSide": {"anyCards": [{"assetId": "oriol-rey-card"}]},
                },
            },
            {
                "amounts": {"eurCents": 750},
                "date": "2026-08-02T12:00:00Z",
                "deal": {
                    "__typename": "TokenOffer", "type": "DIRECT_OFFER",
                    "senderSide": {"anyCards": [{"assetId": "a"}]},
                    "receiverSide": {"anyCards": []},
                },
            },
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
        self.assertEqual(
            [sale["kind"] for sale in payload["sales"]],
            ["auction", "instant", "public", "direct", "trade"],
        )
        self.assertEqual(payload["sales"][2]["label"], "Oferta pública")

    @patch("sorare_utils.build_headers", return_value={})
    @patch("sorare_utils.get_latest_prices")
    def test_bid_review_comparisons_return_latest_sale_for_each_player(self, get_latest, _headers):
        user = get_user_model().objects.create_user(username="comparison-user")
        self.client.force_login(user)
        get_latest.return_value = {
            "oriol-rey-erenas": {
                "amounts": {"eurCents": 718},
                "date": "2026-08-14T06:19:48Z",
                "deal": {"__typename": "TokenAuction"},
            }
        }
        response = self.client.post(
            reverse("auction_bid_comparisons"),
            data=json.dumps({"player_slugs": ["oriol-rey-erenas"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        comparison = response.json()["comparisons"]["oriol-rey-erenas"]
        self.assertEqual(comparison["eur"], 7.18)
        self.assertEqual(comparison["label"], "Subasta")

    @patch("sorare_utils.graphql_request")
    def test_latest_prices_are_batched_in_one_graphql_request(self, graphql_request):
        graphql_request.return_value = {
            "tokens": {
                "p0": [{"amounts": {"eurCents": 718}}],
                "p1": [{"amounts": {"eurCents": 1234}}],
            }
        }
        prices = get_latest_prices(
            ["oriol-rey-erenas", "otro-jugador"],
            rarity="rare", season=2026, headers={},
        )
        self.assertEqual(graphql_request.call_count, 1)
        self.assertEqual(prices["oriol-rey-erenas"]["amounts"]["eurCents"], 718)
        query, variables = graphql_request.call_args.args[:2]
        self.assertIn("p0: tokenPrices", query)
        self.assertIn("p1: tokenPrices", query)
        self.assertEqual(variables["playerSlug1"], "otro-jugador")

    @staticmethod
    def _paths():
        from pathlib import Path
        from web_services.config_files import SorarePaths

        return SorarePaths(repo_root=Path(__file__).resolve().parents[2])


class SalesWorkbenchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="sales-user")
        self.client.force_login(self.user)
        self.available = self._card("available", "Jugador disponible")
        self.blocked = self._card("blocked", "Jugador en lineup", in_lineup=True)
        SalesInventory.objects.create(rarity="rare", cards=[self.available, self.blocked])

    def test_sales_page_hides_blocked_cards_until_requested(self):
        response = self.client.get(reverse("sales_workbench"), {"rarity": "rare"})
        self.assertContains(response, "Jugador disponible")
        self.assertNotContains(response, "Jugador en lineup")

        response = self.client.get(reverse("sales_workbench"), {"rarity": "rare", "show_blocked": "1"})
        self.assertContains(response, "Jugador en lineup")
        self.assertContains(response, "En lineup")

    def test_sales_page_filters_market_minimum_prices(self):
        response = self.client.get(reverse("sales_workbench"), {
            "rarity": "rare", "classic_price_from": "4,01",
        })
        self.assertNotContains(response, "Jugador disponible")

        response = self.client.get(reverse("sales_workbench"), {
            "rarity": "rare", "classic_price_from": "4,00",
        })
        self.assertContains(response, "Jugador disponible")
        self.assertNotContains(response, "Media ventas")

    def test_batch_sale_is_queued_and_rejects_blocked_cards(self):
        blocked = json.dumps([{"asset_id": "blocked", "euros": "4,50", "duration_days": 5}])
        response = self.client.post(reverse("enqueue_batch_sales"), {
            "sales": blocked, "confirm": "on", "request_key": "e5701b64-7728-4f0e-8afb-2209c8785f59",
        })
        self.assertEqual(response.status_code, 409)

        valid = json.dumps([{
            "asset_id": "available", "euros": "4.50",
            "minimum_offer_eur": "3.25", "duration_days": 5,
        }])
        response = self.client.post(reverse("enqueue_batch_sales"), {
            "sales": valid, "confirm": "on", "request_key": "68df42b8-9703-437d-9d83-904ca8d2d3ad",
        })
        self.assertEqual(response.status_code, 202)
        item = SaleBatchItem.objects.get(job_id=response.json()["job_id"])
        self.assertEqual(item.duration_days, 5)
        self.assertEqual(str(item.euros), "4.50")
        self.assertEqual(str(item.minimum_offer_eur), "3.25")

    def test_sale_form_defaults_to_seven_days(self):
        form = BatchSaleForm({
            "sales": json.dumps([{"asset_id": "available", "euros": "4.50"}]),
            "confirm": "on",
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["sales"][0]["duration_days"], 7)
        self.assertIsNone(form.cleaned_data["sales"][0]["minimum_offer_eur"])

    def test_sale_form_rejects_minimum_above_listing_price(self):
        form = BatchSaleForm({
            "sales": json.dumps([{
                "asset_id": "available", "euros": "4.50", "minimum_offer_eur": "5.00",
            }]),
            "confirm": "on",
        })
        self.assertFalse(form.is_valid())

    def test_collection_label_hides_redundant_rarity_and_season(self):
        self.assertEqual(
            collection_display_name("Real Club Celta de Vigo Super Rare 2021-22"),
            "Real Club Celta de Vigo",
        )

    @patch("sorare_utils.build_headers", return_value={})
    @patch("sorare_utils.get_recent_prices")
    def test_sales_history_filters_classic_and_in_season(self, get_prices, _headers):
        get_prices.return_value = [
            {"amounts": {"eurCents": 1000}, "date": "2026-08-17T10:00:00Z", "card": {"seasonYear": 2026, "inSeasonEligible": True}},
            {"amounts": {"eurCents": 800}, "date": "2026-08-16T10:00:00Z", "card": {"seasonYear": 2021, "inSeasonEligible": False}},
        ]
        classic = self.client.get(reverse("sales_price_history"), {
            "player_slug": "denis-suarez", "rarity": "super_rare",
            "mode": "classic", "season_year": 2021,
        })
        self.assertEqual([sale["eur"] for sale in classic.json()["sales"]], [8.0])
        self.assertIsNone(get_prices.call_args.kwargs["season"])
        self.assertEqual(get_prices.call_args.kwargs["season_eligibility"], "CLASSIC")

        in_season = self.client.get(reverse("sales_price_history"), {
            "player_slug": "denis-suarez", "rarity": "super_rare",
            "mode": "in_season", "season_year": 2026,
        })
        self.assertEqual([sale["eur"] for sale in in_season.json()["sales"]], [10.0])
        self.assertEqual(get_prices.call_args.kwargs["season"], 2026)
        self.assertEqual(get_prices.call_args.kwargs["season_eligibility"], "IN_SEASON")

    @patch("dashboard.management.commands.process_sales_queue.run_card_sale")
    def test_sale_worker_uses_duration_and_marks_cached_card_as_listed(self, run_sale):
        run_sale.return_value = ScriptResult("mock", 0, "Oferta creada", "")
        job = SaleBatchJob.objects.create(user=self.user, total_count=1)
        SaleBatchItem.objects.create(
            job=job, position=1, asset_id="available", player_name="Jugador disponible",
            rarity="rare", euros="4.50", minimum_offer_eur="3.25", duration_days=7,
        )

        process_next_sale()

        run_sale.assert_called_once()
        self.assertEqual(run_sale.call_args.kwargs["duration_days"], 7)
        self.assertEqual(run_sale.call_args.kwargs["minimum_offer_eur"], "3.25")
        job.refresh_from_db()
        self.assertEqual(job.status, SaleBatchJob.Status.SUCCEEDED)
        cached = SalesInventory.objects.get(rarity="rare").cards[0]
        self.assertTrue(cached["active_listing"])
        self.assertTrue(cached["blocked"])

    @patch("web_services.process_runner._run_command")
    def test_card_sale_passes_private_trade_minimum_in_cents(self, run_command):
        from web_services.config_files import SorarePaths

        paths = SorarePaths(repo_root=Path(__file__).resolve().parents[2])
        run_card_sale(
            paths,
            asset_id="asset-1",
            euros="4.50",
            minimum_offer_eur="3.25",
            duration_days=7,
        )
        command = run_command.call_args.args[0]
        self.assertEqual(command[3:6], ["450", "7", "325"])

    def test_card_sale_uses_private_minimum_mutation(self):
        script = (Path(__file__).resolve().parents[2] / "javascript" / "vender_carta.js").read_text()
        self.assertIn("isPrivate: true", script)
        self.assertIn("privateMinPrices", script)

    def test_sales_review_allows_removing_an_item(self):
        response = self.client.get(reverse("sales_workbench"), {"rarity": "rare"})
        self.assertContains(response, "Quitar esta venta")

    @patch("dashboard.management.commands.process_sales_queue.collect_sales_inventory")
    def test_refresh_replaces_only_the_selected_rarity_inventory(self, collect):
        collect.return_value = [self._card("fresh", "Carta nueva")]
        job = SalesRefreshJob.objects.create(user=self.user, rarity="rare")

        process_next_refresh()

        job.refresh_from_db()
        self.assertEqual(job.status, SalesRefreshJob.Status.SUCCEEDED)
        self.assertEqual(job.card_count, 1)
        self.assertEqual(SalesInventory.objects.get(rarity="rare").cards[0]["asset_id"], "fresh")

    def test_refresh_status_returns_real_percentage(self):
        job = SalesRefreshJob.objects.create(
            user=self.user, rarity="rare", status=SalesRefreshJob.Status.RUNNING,
            processed_count=3, total_count=12, progress_label="Oriol Rey",
        )
        response = self.client.get(reverse("sales_jobs_status"))
        progress = next(item for item in response.json()["refreshes"] if item["id"] == job.id)
        self.assertEqual(progress["percent"], 25)
        self.assertEqual(progress["processed_count"], 3)
        self.assertEqual(progress["progress_label"], "Oriol Rey")

    def test_navigating_to_another_page_does_not_cancel_refresh(self):
        job = SalesRefreshJob.objects.create(
            user=self.user, rarity="super_rare", status=SalesRefreshJob.Status.RUNNING,
            processed_count=2, total_count=8,
        )
        response = self.client.get(reverse("index"))
        self.assertContains(response, "globalSalesProgress")
        job.refresh_from_db()
        self.assertEqual(job.status, SalesRefreshJob.Status.RUNNING)

    @staticmethod
    def _card(asset_id, player, *, in_lineup=False):
        blocked = in_lineup
        return {
            "asset_id": asset_id, "player": player, "player_slug": player.lower().replace(" ", "-"),
            "player_picture_url": "", "team": "Equipo", "team_picture_url": "",
            "rarity": "rare", "season": "2026-27", "position": "Defender", "league": "LaLiga",
            "grade": 2, "in_season": True, "collection_name": "Colección",
            "collection_rays": 100, "card_rays": 10, "rays_after_sale": 90,
            "avg_price": 5.0, "recent_sales_count": 5, "min_price_classic": 4.0,
            "min_price_inseason": 6.0, "in_lineup": in_lineup, "in_vault": False,
            "active_listing": False, "blocked": blocked,
            "blocked_reason": "En lineup" if blocked else "",
        }


class MovementHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="burguis", password="test")
        self.client.force_login(self.user)

    @staticmethod
    def _api_card(*, player="Carles Aleñá", rarity="rare", in_season=True, league="laliga-ea-sports"):
        return {
            "assetId": f"asset-{player}", "slug": player.lower(), "name": player,
            "rarityTyped": rarity, "seasonYear": 2026, "inSeasonEligible": in_season,
            "pictureUrl": "", "anyPlayer": {"slug": player.lower(), "displayName": player, "squaredPictureUrl": "player.png"},
            "anyTeam": {"name": "Deportivo Alavés", "pictureUrl": "team.png", "domesticLeague": {"slug": league, "displayName": "LALIGA EA SPORTS" if "laliga" in league else "J1 League"}},
        }

    def test_sale_reports_buyer_price_received_amount_and_fee(self):
        card = self._api_card()
        operation = {
            "__typename": "TokenOffer", "id": "offer-1", "transactionDate": "2026-08-20T10:00:00Z",
            "type": "SINGLE_BUY_OFFER", "settlementCurrencies": ["EUR"],
            "userBuyer": {"slug": "other"}, "userSeller": {"slug": "burguis"},
            "marketFeeAmounts": {"eurCents": 500, "referenceCurrency": "EUR"},
            "senderSide": {"amounts": {"eurCents": 0}, "anyCards": [card]},
            "receiverSide": {"amounts": {"eurCents": 10000, "referenceCurrency": "EUR"}, "anyCards": []},
        }
        movement = _movement_from_group([{
            "id": "entry-1", "date": "2026-08-20T10:00:00Z", "entryType": "PAYMENT",
            "amounts": {"eurCents": 9500, "referenceCurrency": "EUR"}, "tokenOperation": operation,
        }], "burguis")
        self.assertEqual(movement["direction"], "sale")
        self.assertEqual(movement["market"], "Oferta pública")
        self.assertEqual((movement["gross_eur"], movement["net_eur"], movement["fee_eur"]), (100.0, 95.0, 5.0))
        self.assertEqual(movement["category"], "laliga_inseason")

    def test_public_offer_is_identified_by_the_seller_accepting_it(self):
        card = self._api_card(player="Mathew Ryan")
        operation = {
            "__typename": "TokenOffer", "id": "offer-mathew", "transactionDate": "2026-08-20T07:01:00Z",
            "type": "SINGLE_SALE_OFFER", "userAcceptor": {"slug": "burguis"},
            "userBuyer": {"slug": "buyer"}, "userSeller": {"slug": "burguis"},
            "marketFeeAmounts": {"eurCents": 415, "referenceCurrency": "EUR"},
            "senderSide": {"amounts": {"eurCents": 0}, "anyCards": [card]},
            "receiverSide": {"amounts": {"eurCents": 8300, "referenceCurrency": "EUR"}, "anyCards": []},
        }

        movement = _movement_from_group([{
            "id": "offer-mathew", "date": operation["transactionDate"], "entryType": "PAYMENT",
            "amounts": operation["receiverSide"]["amounts"], "tokenOperation": operation,
        }], "burguis")

        self.assertEqual(movement["market"], "Oferta pública")

    def test_instant_buy_is_identified_by_the_buyer_accepting_it(self):
        card = self._api_card(player="Venta de mercado")
        operation = {
            "__typename": "TokenOffer", "id": "offer-listing", "transactionDate": "2026-08-20T07:01:00Z",
            "type": "SINGLE_SALE_OFFER", "userAcceptor": {"slug": "buyer"},
            "userBuyer": {"slug": "buyer"}, "userSeller": {"slug": "burguis"},
            "marketFeeAmounts": {"eurCents": 50, "referenceCurrency": "EUR"},
            "senderSide": {"amounts": {"eurCents": 0}, "anyCards": [card]},
            "receiverSide": {"amounts": {"eurCents": 1000, "referenceCurrency": "EUR"}, "anyCards": []},
        }

        movement = _movement_from_group([{
            "id": "offer-listing", "date": operation["transactionDate"], "entryType": "PAYMENT",
            "amounts": operation["receiverSide"]["amounts"], "tokenOperation": operation,
        }], "burguis")

        self.assertEqual(movement["market"], "Compra instantánea")

    def test_cash_and_card_offer_separates_sent_and_received_cards(self):
        sivera = self._api_card(player="Sivera")
        sivera["assetId"], sivera["serialNumber"] = "sivera-asset", 10
        dituro = self._api_card(player="Matías Dituro")
        dituro["assetId"], dituro["serialNumber"] = "dituro-asset", 14
        operation = {
            "__typename": "TokenOffer", "id": "swap-1", "transactionDate": "2026-08-19T11:02:00Z",
            "type": "DIRECT_OFFER", "userBuyer": {"slug": "other"}, "userSeller": {"slug": "burguis"},
            "sender": {"slug": "burguis"}, "receiver": {"slug": "other"},
            "marketFeeAmounts": {"eurCents": 225, "referenceCurrency": "EUR"},
            "senderSide": {"amounts": {"eurCents": 0}, "anyCards": [sivera]},
            "receiverSide": {"amounts": {"eurCents": 4500, "referenceCurrency": "EUR"}, "anyCards": [dituro]},
        }
        movement = _movement_from_group([{
            "id": "swap-1", "date": operation["transactionDate"], "entryType": "PAYMENT",
            "amounts": operation["receiverSide"]["amounts"], "tokenOperation": operation,
        }], "burguis")
        self.assertEqual(movement["direction"], "trade")
        self.assertEqual(movement["cash_direction"], "sale")
        self.assertEqual([card["player"] for card in movement["sent_cards"]], ["Sivera"])
        self.assertEqual([card["player"] for card in movement["received_cards"]], ["Matías Dituro"])
        self.assertEqual((movement["gross_eur"], movement["net_eur"]), (45.0, 42.75))

    def test_mixed_trade_stays_in_laliga_when_one_card_is_relevant(self):
        yangel = self._api_card(player="Yangel Herrera")
        classic = self._api_card(player="Carta Classic", in_season=False, league="j1-league")
        operation = {
            "__typename": "TokenOffer", "id": "yangel-trade", "transactionDate": "2026-08-20T21:44:44Z",
            "type": "DIRECT_OFFER", "userBuyer": {"slug": "other"}, "userSeller": {"slug": "burguis"},
            "sender": {"slug": "burguis"}, "receiver": {"slug": "other"},
            "marketFeeAmounts": {"eurCents": 22, "referenceCurrency": "EUR"},
            "senderSide": {"amounts": {"eurCents": 0}, "anyCards": [yangel]},
            "receiverSide": {"amounts": {"eurCents": 448, "referenceCurrency": "EUR"}, "anyCards": [classic]},
        }

        movement = _movement_from_group([{
            "id": "yangel-trade", "date": operation["transactionDate"], "entryType": "PAYMENT",
            "amounts": operation["receiverSide"]["amounts"], "tokenOperation": operation,
        }], "burguis")

        self.assertEqual(movement["category"], "laliga_inseason")

    def test_classic_card_goes_to_other_movements(self):
        card = self._api_card(player="Take", in_season=False, league="j1-league")
        operation = {
            "__typename": "TokenBid", "id": "bid-1", "createdAt": "2026-08-19T10:00:00Z",
            "fiatPayment": True, "amounts": {"eurCents": 650, "referenceCurrency": "EUR"},
            "conversionCredit": None, "auction": {"transactionDate": "2026-08-19T10:00:00Z", "currency": "EUR", "anyCards": [card]},
        }
        movement = _movement_from_group([{
            "id": "entry-2", "date": "2026-08-19T10:00:00Z", "entryType": "PAYMENT",
            "amounts": {"eurCents": -650, "referenceCurrency": "EUR"}, "tokenOperation": operation,
        }], "burguis")
        self.assertEqual(movement["category"], "other")
        self.assertEqual(movement["gross_eur"], 6.5)

    def test_classic_laliga_offer_is_kept_in_laliga_movements(self):
        camavinga = self._api_card(player="Eduardo Camavinga", in_season=False)
        camavinga["assetId"] = "camavinga-73"
        camavinga["seasonYear"] = 2021
        camavinga["serialNumber"] = 73
        operation = {
            "__typename": "TokenOffer",
            "id": "SingleBuyOffer:4dbebfb2-abfd-4d9a-92fb-b2d3077b8227",
            "transactionDate": "2026-08-23T08:19:25Z", "type": "SINGLE_BUY_OFFER",
            "paymentCurrency": "", "settlementCurrencies": ["WEI"],
            "userAcceptor": {"slug": "marcel09"}, "userBuyer": {"slug": "burguis"},
            "userSeller": {"slug": "marcel09"}, "sender": {"slug": "burguis"},
            "receiver": {"slug": "marcel09"},
            "senderSide": {"amounts": {"eurCents": 552, "wei": "2700000000000000", "referenceCurrency": "WEI"}, "anyCards": []},
            "receiverSide": {"amounts": {"eurCents": 0, "wei": "0", "referenceCurrency": "WEI"}, "anyCards": [camavinga]},
            "marketFeeAmounts": {"eurCents": 20, "wei": "100000000000000", "referenceCurrency": "WEI"},
        }

        movement = _movement_from_group([{
            "id": operation["id"], "date": operation["transactionDate"], "entryType": "PAYMENT",
            "amounts": operation["senderSide"]["amounts"], "tokenOperation": operation,
        }], "burguis")

        self.assertEqual(movement["direction"], "purchase")
        self.assertEqual(movement["cards"][0]["player"], "Eduardo Camavinga")
        self.assertFalse(movement["cards"][0]["in_season"])
        self.assertEqual(movement["category"], "laliga_inseason")
        # settlementCurrencies=WEI only describes how the offer can settle;
        # without the user's confirmed account entry it does not prove how it was paid.
        self.assertEqual(movement["currency"], "")

    def test_open_bid_is_not_a_completed_movement(self):
        operation = {
            "__typename": "TokenBid", "id": "live-bid", "createdAt": "2026-08-21T10:00:00Z",
            "amounts": {"eurCents": 577, "wei": "3000000000000000", "referenceCurrency": "WEI"},
            "conversionCredit": None,
            "auction": {"id": "auction-live", "transactionDate": None, "anyCards": [self._api_card()]},
        }
        movement = _movement_from_group([{
            "id": "provisional", "date": "2026-08-21T10:00:00Z",
            "entryType": "PAYMENT", "amounts": operation["amounts"], "tokenOperation": operation,
        }], "burguis")
        self.assertIsNone(movement)

    def test_completed_auction_uses_full_price_without_credit_estimates(self):
        trade = {
            "__typename": "TokenAuction", "id": "auction-won", "transactionDate": "2026-08-20T10:00:00Z",
            "currency": "EUR",
            "anyCards": [self._api_card()],
            "bestBid": {
                "id": "winning-bid", "fiatPayment": False,
                "amounts": {
                    "eurCents": 650, "wei": "3900000000000000",
                    "referenceCurrency": "WEI",
                },
                "conversionCredit": {
                    "id": "rodrygo-credit", "status": "USED",
                    "purchase": {"__typename": "TokenAuction", "id": "auction-won"},
                    "totalDiscount": {"eurCents": 100, "referenceCurrency": "EUR"},
                },
            },
        }
        operation = _operation_from_trade(trade)
        operation["paymentCurrency"] = "EUR"
        movement = _movement_from_group([{
            "id": "winning-bid", "date": trade["transactionDate"], "entryType": "PAYMENT",
            "amounts": operation["amounts"], "tokenOperation": operation,
        }], "burguis")
        self.assertEqual(movement["gross_eur"], 6.5)
        self.assertEqual(movement["credits_eur"], 0)
        self.assertEqual(movement["net_eur"], 6.5)
        self.assertEqual(movement["currency"], "EUR")

    def test_completed_auction_ignores_incomplete_credit_metadata(self):
        operation = {
            "__typename": "TokenBid", "id": "credits-bid", "createdAt": "2026-08-20T10:00:00Z",
            "fiatPayment": True, "paymentCurrency": "EUR", "paidEur": 7,
            "amounts": {"eurCents": 1000, "referenceCurrency": "EUR"},
            "conversionCredit": None,
            "conversionCredits": [
                {"id": "credit-a", "totalDiscount": {"eurCents": 200, "referenceCurrency": "EUR"}},
                {"id": "credit-b", "totalDiscount": {"eurCents": 100, "referenceCurrency": "EUR"}},
            ],
            "auction": {"transactionDate": "2026-08-20T10:00:00Z", "anyCards": [self._api_card()]},
        }

        movement = _movement_from_group([{
            "id": "credits-bid", "date": operation["createdAt"], "entryType": "PAYMENT",
            "amounts": operation["amounts"], "tokenOperation": operation,
        }], "burguis")

        self.assertEqual(movement["gross_eur"], 10)
        self.assertEqual(movement["credits_eur"], 0)
        self.assertEqual(movement["net_eur"], 10)

        operation.pop("paidEur")
        operation["auction"]["id"] = "auction-credits"
        operation["conversionCredits"] = [
            {
                "id": "used-on-this-auction", "status": "USED",
                "purchase": {"__typename": "TokenAuction", "id": "auction-credits"},
                "totalDiscount": {"eurCents": 200, "referenceCurrency": "EUR"},
            },
            {
                "id": "used-elsewhere", "status": "USED",
                "purchase": {"__typename": "TokenAuction", "id": "another-auction"},
                "totalDiscount": {"eurCents": 74261, "referenceCurrency": "EUR"},
            },
        ]
        movement = _movement_from_group([{
            "id": "credits-bid", "date": operation["createdAt"], "entryType": "PAYMENT",
            "amounts": operation["amounts"], "tokenOperation": operation,
        }], "burguis")
        self.assertEqual(movement["credits_eur"], 0)
        self.assertEqual(movement["net_eur"], 10)

        operation["conversionCredits"] = [{
            "id": "reusable-credit",
            "totalDiscount": {"eurCents": 74261, "referenceCurrency": "EUR"},
        }]
        movement = _movement_from_group([{
            "id": "credits-bid", "date": operation["createdAt"], "entryType": "PAYMENT",
            "amounts": operation["amounts"], "tokenOperation": operation,
        }], "burguis")
        self.assertEqual(movement["credits_eur"], 0)
        self.assertEqual(movement["net_eur"], 10)

    def test_completed_crypto_auction_reports_eth(self):
        operation = {
            "__typename": "TokenBid", "id": "crypto-bid", "createdAt": "2026-08-20T10:00:00Z",
            "fiatPayment": False,
            "paymentCurrency": "ETH",
            "amounts": {
                "eurCents": 650, "wei": "3900000000000000",
                "referenceCurrency": "WEI",
            },
            "conversionCredit": None,
            "auction": {"transactionDate": "2026-08-20T10:00:00Z", "currency": "WEI", "anyCards": [self._api_card()]},
        }

        movement = _movement_from_group([{
            "id": "crypto-bid", "date": "2026-08-20T10:00:00Z", "entryType": "PAYMENT",
            "amounts": operation["amounts"], "tokenOperation": operation,
        }], "burguis")

        self.assertEqual(movement["currency"], "ETH")

    def test_payment_account_identifies_wallet_currency_without_using_reference_currency(self):
        fiat_entry = {
            "account": {"accountable": {"__typename": "FiatWalletAccount", "currency": "EUR"}},
        }
        eth_entry = {
            "account": {"accountable": {"__typename": "StarkwareAccount"}},
        }

        self.assertEqual(_account_currency(fiat_entry), "EUR")
        self.assertEqual(_account_currency(eth_entry), "ETH")

    def test_essence_reward_reports_quantity_instead_of_card_or_eur(self):
        operation = {
            "__typename": "So5Reward", "id": "essence-reward", "amount": None,
            "rewardCards": [],
            "rewards": [{
                "__typename": "CardShardsReward", "quantity": 20, "rarity": "rare",
            }],
            "rewardConfigs": [],
            "so5Fixture": {"gameWeek": 5, "shortDisplayName": "GW5"},
            "so5Leaderboard": {"displayName": "LALIGA – Rare"},
            "so5Ranking": {"ranking": 194},
        }
        movement = _movement_from_group([{
            "id": "essence-entry", "date": "2026-08-18T20:00:00Z", "entryType": "REWARD",
            "amounts": {}, "tokenOperation": operation,
        }], "burguis")
        movement["market"] = "Recompensa de jornada · GW5 · LALIGA – Rare · Puesto 194"

        self.assertEqual(movement["essence_quantity"], 20)
        self.assertEqual(movement["essence_description"], "Rare")
        self.assertEqual(movement["reward_type"], "essence")
        self.assertEqual(movement["reward_rarity"], "rare")
        self.assertEqual(movement["currency"], "")

        MovementSnapshot.objects.create(user=self.user, movements=[movement], source_version=14)
        response = self.client.get(reverse("movements"), {"category": "reward", "reward_type": "essence"})
        self.assertContains(response, "+20 Esencia")
        self.assertContains(response, "reward-kind reward-essence rarity-rare")
        self.assertNotContains(response, ">Carta<")

    def test_trade_cycles_match_exact_card_and_calculate_balance(self):
        card = {"asset_id": "mathew-asset", "player": "Mathew Ryan", "player_slug": "mathew-ryan", "rarity": "rare", "season_year": 2026, "in_season": True, "serial_number": 11}
        purchase = {"id": "buy", "occurred_at": "2026-08-14T10:35:00Z", "direction": "purchase", "cash_direction": "purchase", "market": "Subasta", "cards": [card], "received_cards": [card], "sent_cards": [], "net_eur": 71.76, "credits_eur": 0}
        sale = {"id": "sell", "occurred_at": "2026-08-20T07:01:00Z", "direction": "sale", "cash_direction": "sale", "market": "Compra instantánea", "cards": [card], "received_cards": [], "sent_cards": [card], "gross_eur": 83, "net_eur": 78.85, "fee_eur": 4.15}
        cycles = build_trade_cycles([sale, purchase])
        self.assertEqual(len(cycles), 1)
        self.assertTrue(cycles[0]["exact_card"])
        self.assertEqual(cycles[0]["balance_eur"], Decimal("7.09"))

    def test_trade_cycles_warn_when_matching_a_different_serial(self):
        bought = {"asset_id": "zakaria-16", "player": "Zakaria Eddahchouri", "player_slug": "zakaria", "rarity": "rare", "season_year": 2026, "in_season": True, "serial_number": 16}
        sold = dict(bought, asset_id="zakaria-10", serial_number=10)
        purchase = {"id": "buy-z", "occurred_at": "2026-08-10T10:00:00Z", "direction": "purchase", "cash_direction": "purchase", "market": "Oferta directa", "cards": [bought], "received_cards": [bought], "sent_cards": [], "net_eur": 7.56}
        sale = {"id": "sell-z", "occurred_at": "2026-08-20T10:00:00Z", "direction": "sale", "cash_direction": "sale", "market": "Venta", "cards": [sold], "received_cards": [], "sent_cards": [sold], "gross_eur": 5.4, "net_eur": 5.4, "fee_eur": 0}
        cycle = build_trade_cycles([purchase, sale])[0]
        self.assertFalse(cycle["exact_card"])
        self.assertIn("compra #16 · venta #10", cycle["notes"][0])

    def test_trade_cycles_can_group_a_repurchase_after_the_sale(self):
        bought = {"asset_id": "zakaria-19", "player": "Zakaria Eddahchouri", "player_slug": "zakaria", "rarity": "rare", "season_year": 2026, "in_season": True, "serial_number": 19}
        sold = dict(bought, asset_id="zakaria-16", serial_number=16)
        sale = {"id": "sell-z", "occurred_at": "2026-08-20T00:00:00Z", "direction": "sale", "cash_direction": "sale", "market": "Oferta pública", "cards": [sold], "received_cards": [], "sent_cards": [sold], "gross_eur": 7.56, "net_eur": 7.2, "fee_eur": .36}
        purchase = {"id": "buy-z", "occurred_at": "2026-08-20T00:20:00Z", "direction": "purchase", "cash_direction": "purchase", "market": "Subasta", "cards": [bought], "received_cards": [bought], "sent_cards": [], "net_eur": 5.4}

        cycle = build_trade_cycles([purchase, sale])[0]

        self.assertTrue(cycle["purchase_after_sale"])
        self.assertEqual(cycle["balance_eur"], Decimal("1.80"))
        self.assertIn("compra #19 · venta #16", cycle["notes"][0])
        self.assertIn("compra es posterior", cycle["notes"][1])

    def test_trade_cycle_includes_later_sales_of_received_cards(self):
        yangel = {
            "asset_id": "yangel-23", "player": "Yangel Herrera", "player_slug": "yangel-herrera",
            "team": "Girona FC", "rarity": "rare", "season_year": 2026, "in_season": True,
            "is_laliga": True, "serial_number": 23,
        }
        hugo = {
            "asset_id": "hugo-17", "player": "Hugo Cuenca", "player_slug": "hugo-cuenca",
            "team": "Genoa", "rarity": "rare", "season_year": 2026, "in_season": True,
            "is_laliga": False, "serial_number": 17,
        }
        ruben = {
            "asset_id": "ruben-110", "player": "Rubén Sánchez", "player_slug": "ruben-sanchez",
            "team": "Espanyol", "rarity": "limited", "season_year": 2026, "in_season": True,
            "is_laliga": True, "serial_number": 110,
        }
        purchase = {
            "id": "yangel-buy", "occurred_at": "2026-08-20T20:21:25Z", "direction": "purchase",
            "cash_direction": "purchase", "market": "Subasta", "category": "laliga_inseason",
            "cards": [yangel], "received_cards": [yangel], "sent_cards": [], "net_eur": 20,
        }
        trade = {
            "id": "yangel-trade", "occurred_at": "2026-08-20T21:44:44Z", "direction": "trade",
            "cash_direction": "sale", "market": "Intercambio + dinero", "category": "laliga_inseason",
            "cards": [yangel, hugo, ruben], "received_cards": [hugo, ruben], "sent_cards": [yangel],
            "gross_eur": 4.48, "net_eur": 4.26, "fee_eur": .22,
        }
        hugo_sale = {
            "id": "hugo-sale", "occurred_at": "2026-08-22T10:00:00Z", "direction": "sale",
            "cash_direction": "sale", "market": "Oferta pública", "category": "other",
            "cards": [hugo], "received_cards": [], "sent_cards": [hugo], "gross_eur": 3.2,
            "net_eur": 3.04, "fee_eur": .16,
        }

        cycles = build_trade_cycles([purchase, trade, hugo_sale])

        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["sale_card"]["player"], "Yangel Herrera")
        self.assertEqual(cycles[0]["realized_proceeds_eur"], Decimal("7.30"))
        self.assertEqual(cycles[0]["balance_eur"], Decimal("-12.70"))
        self.assertEqual([item["card"]["player"] for item in cycles[0]["derived_sales"]], ["Hugo Cuenca"])
        self.assertEqual([card["player"] for card in cycles[0]["pending_received_cards"]], ["Rubén Sánchez"])
        self.assertEqual(set(cycles[0]["movement_ids"]), {"yangel-buy", "yangel-trade", "hugo-sale"})
        self.assertFalse(cycles[0]["is_complete"])

    @patch("web_services.movement_history.graphql_request")
    def test_collector_uses_completed_trades_and_discards_open_auctions(self, request):
        card = self._api_card()
        completed = {
            "__typename": "TokenAuction", "id": "auction-completed",
            "transactionDate": "2026-08-20T10:00:00Z", "currency": "EUR",
            "anyCards": [{"assetId": card["assetId"]}],
            "bestBid": {"id": "bid-completed", "fiatPayment": True, "amounts": {"eurCents": 650, "referenceCurrency": "EUR"}, "conversionCredit": None},
        }
        open_auction = dict(completed, id="auction-open", transactionDate=None)

        def response(query, variables, headers=None):
            if "CompletedTrades" in query:
                return {"currentUser": {"slug": "burguis", "trades": {"nodes": [open_auction, completed], "pageInfo": {"hasNextPage": False}}}}
            if "MovementConversionCredits" in query:
                return {"currentUser": {"sportConversionCredits": {"nodes": [{
                    "id": "rodrygo-credit", "status": "USED",
                    "totalDiscount": {"eurCents": 100, "referenceCurrency": "EUR"},
                    "purchase": {"__typename": "TokenAuction", "id": "auction-completed"},
                }], "pageInfo": {"hasNextPage": False}}}}
            if "MovementPaymentAccounts" in query:
                if variables.get("currencies") != ["EUR"]:
                    return {"currentUser": {"accountEntries": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
                return {"currentUser": {"accountEntries": {"nodes": [{
                    "id": "payment-entry", "provisional": False, "aasmState": "CONFIRMED",
                    "account": {"accountable": {"__typename": "FiatWalletAccount", "currency": "EUR"}},
                    "amounts": {"eurCents": 650, "referenceCurrency": "EUR"},
                    "tokenOperation": {"__typename": "TokenBid", "id": "bid-completed"},
                }], "pageInfo": {"hasNextPage": False}}}}
            if "GameweekRewards" in query:
                return {"so5": {"allSo5Fixtures": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "MovementCards" in query:
                return {"tokens": {"anyCards": [card]}}
            return {"currentUser": {"accountEntries": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}

        request.side_effect = response
        movements = collect_movement_history(headers={"Authorization": "test"})
        self.assertEqual([movement["id"] for movement in movements], ["bid-completed"])
        self.assertEqual(movements[0]["currency"], "EUR")
        self.assertEqual(movements[0]["credits_eur"], 0)
        self.assertEqual(movements[0]["net_eur"], 6.5)

    @patch("web_services.movement_history.graphql_request")
    def test_collector_recovers_real_eur_currency_without_estimating_credits(self, request):
        rodrygo = self._api_card(player="Rodrygo")
        rodrygo.update({"assetId": "rodrygo-16", "seasonYear": 2026, "serialNumber": 16})
        rioja = self._api_card(player="L. Rioja")
        rioja.update({"assetId": "rioja-9", "seasonYear": 2026, "serialNumber": 9})
        trades = [
            {
                "__typename": "TokenAuction",
                "id": "EnglishAuction:569601ec-31de-49b9-9500-8e30e05632f5",
                "transactionDate": "2026-08-13T21:49:11Z",
                "anyCards": [{"assetId": "rodrygo-16"}],
                "bestBid": {
                    "id": "Bid:bfa83679-3fe7-4be5-a8d6-ce8ff4196900",
                    "fiatPayment": False,
                    "amounts": {"eurCents": 2403, "wei": "14700000000000000", "referenceCurrency": "WEI"},
                    "conversionCredit": None,
                    "conversionCredits": [],
                },
            },
            {
                "__typename": "TokenAuction",
                "id": "EnglishAuction:ae63cf9d-ca4c-4579-b813-bb5d487e4045",
                "transactionDate": "2026-08-12T19:40:36Z",
                "anyCards": [{"assetId": "rioja-9"}],
                "bestBid": {
                    "id": "Bid:9be628bf-e169-4905-91e5-8830f1a519e4",
                    "fiatPayment": False,
                    "amounts": {"eurCents": 1901, "wei": "11600000000000000", "referenceCurrency": "WEI"},
                    "conversionCredit": None,
                    "conversionCredits": [],
                },
            },
        ]
        eur_entries = [
            {
                "id": "rodrygo-eur-payment", "provisional": False, "aasmState": "CONFIRMED",
                "amounts": {"eurCents": 1202, "referenceCurrency": "EUR"},
                "account": {"accountable": {"__typename": "CommonAccount"}},
                "tokenOperation": {"__typename": "TokenBid", "id": trades[0]["bestBid"]["id"]},
            },
            {
                "id": "rioja-eur-payment", "provisional": False, "aasmState": "CONFIRMED",
                "amounts": {"eurCents": 950, "referenceCurrency": "EUR"},
                "account": {"accountable": {"__typename": "CommonAccount"}},
                "tokenOperation": {"__typename": "TokenBid", "id": trades[1]["bestBid"]["id"]},
            },
        ]

        def response(query, variables, headers=None):
            if "CompletedTrades" in query:
                return {"currentUser": {"slug": "burguis", "trades": {"nodes": trades, "pageInfo": {"hasNextPage": False}}}}
            if "MovementConversionCredits" in query:
                return {"currentUser": {"sportConversionCredits": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "MovementPaymentAccounts" in query:
                nodes = eur_entries if variables.get("currencies") == ["EUR"] else []
                return {"currentUser": {"accountEntries": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}}
            if "GameweekRewards" in query:
                return {"so5": {"allSo5Fixtures": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "RewardEntries" in query:
                return {"currentUser": {"accountEntries": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "MovementCards" in query:
                return {"tokens": {"anyCards": [rodrygo, rioja]}}
            raise AssertionError("Consulta inesperada")

        request.side_effect = response
        movements = collect_movement_history(headers={"Authorization": "test"})
        by_player = {movement["cards"][0]["player"]: movement for movement in movements}

        self.assertEqual(by_player["Rodrygo"]["currency"], "EUR")
        self.assertEqual(by_player["Rodrygo"]["credits_eur"], 0)
        self.assertEqual(by_player["Rodrygo"]["net_eur"], 24.03)
        self.assertEqual(by_player["L. Rioja"]["currency"], "EUR")
        self.assertEqual(by_player["L. Rioja"]["credits_eur"], 0)
        self.assertEqual(by_player["L. Rioja"]["net_eur"], 19.01)

    @patch("dashboard.management.commands.process_sales_queue.collect_movement_history")
    def test_background_sync_replaces_snapshot_only_after_success(self, collect):
        collect.return_value = [{"id": "movement-1", "category": "laliga_inseason"}]
        job = MovementSyncJob.objects.create(user=self.user)
        process_next_movement_sync()
        job.refresh_from_db()
        self.assertEqual(job.status, MovementSyncJob.Status.SUCCEEDED)
        snapshot = MovementSnapshot.objects.get(user=self.user)
        self.assertEqual(snapshot.movements[0]["id"], "movement-1")
        self.assertEqual(snapshot.source_version, 14)

    @patch("web_services.movement_history.graphql_request")
    def test_collector_adds_confirmed_gameweek_rewards(self, request):
        card = self._api_card(player="Premio de jornada")
        reward_operation = {
            "__typename": "So5Reward", "id": "reward-1", "slug": "reward-1",
            "amount": {"eurCents": 500, "referenceCurrency": "EUR"},
            "rewardCards": [{"anyCard": {"assetId": card["assetId"]}}],
            "so5Fixture": {
                "gameWeek": 12, "shortDisplayName": "GW 12",
                "rewardsDeliveryDate": "2026-08-20T12:00:00Z",
            },
            "so5Leaderboard": {"displayName": "Rare"},
            "so5Ranking": {"ranking": 25},
        }

        def response(query, _variables, headers=None):
            if "CompletedTrades" in query:
                return {"currentUser": {"slug": "burguis", "trades": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "MovementConversionCredits" in query:
                return {"currentUser": {"sportConversionCredits": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "MovementPaymentAccounts" in query:
                return {"currentUser": {"accountEntries": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "GameweekRewards" in query:
                return {"so5": {"allSo5Fixtures": {"nodes": [{
                    "id": "fixture-12", "mySo5Rewards": [reward_operation],
                }], "pageInfo": {"hasNextPage": False}}}}
            if "RewardEntries" in query:
                return {"currentUser": {"accountEntries": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}
            if "MovementCards" in query:
                return {"tokens": {"anyCards": [card]}}
            raise AssertionError("Consulta inesperada")

        request.side_effect = response
        movements = collect_movement_history(headers={"Authorization": "test"})

        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0]["direction"], "reward")
        self.assertEqual(movements[0]["category"], "reward")
        self.assertEqual(movements[0]["market"], "GW 12 · Rare · Puesto 25")
        self.assertEqual(movements[0]["gross_eur"], 5.0)

    @patch("web_services.movement_history.graphql_request")
    def test_public_reward_collector_reads_another_managers_actual_rewards(self, request):
        card = self._api_card(player="Carta pública")
        reward = {
            "__typename": "So5Reward", "id": "public-reward", "slug": "public-reward",
            "aasmState": "claimed", "amount": None, "rewardCards": [],
            "rewards": [{"__typename": "CardShardsReward", "quantity": 510, "rarity": "rare"}],
            "rewardConfigs": [],
            "so5Fixture": {"gameWeek": 5, "shortDisplayName": "GW5", "rewardsDeliveryDate": "2026-08-18T20:00:00Z"},
            "so5Leaderboard": {"displayName": "Champ. – Rare"},
            "so5Ranking": {"ranking": 66},
        }

        def response(query, _variables, headers=None):
            if "PublicManagerRewards" in query:
                return {
                    "user": {"slug": "blasco93", "nickname": "Blasco93"},
                    "so5": {"allSo5Fixtures": {
                        "nodes": [{
                            "__typename": "So5Fixture", "endDate": "2026-08-18T13:59:59Z",
                            "rewardsDeliveryDate": "2026-08-18T20:00:00Z",
                            "userFixtureResults": {"eligibleOrSo5Rewards": [reward]},
                        }],
                        "pageInfo": {"hasPreviousPage": False, "startCursor": None},
                    }},
                }
            if "MovementCards" in query:
                return {"tokens": {"anyCards": [card]}}
            raise AssertionError("Consulta inesperada")

        request.side_effect = response
        result = collect_public_reward_history("blasco93", headers={"Authorization": "test"})

        self.assertEqual(result["manager_nickname"], "Blasco93")
        self.assertEqual(len(result["movements"]), 1)
        self.assertEqual(result["movements"][0]["essence_quantity"], 510)
        self.assertEqual(result["movements"][0]["market"], "GW5 · Champ. – Rare · Puesto 66")

    @patch("dashboard.management.commands.process_sales_queue.collect_public_reward_history")
    def test_public_reward_sync_saves_snapshot_in_background(self, collect):
        collect.return_value = {
            "manager_slug": "blasco93", "manager_nickname": "Blasco93",
            "movements": [{"id": "reward-1", "category": "reward"}],
        }
        job = PublicRewardSyncJob.objects.create(user=self.user, manager_slug="blasco93")

        process_next_public_reward_sync()

        job.refresh_from_db()
        self.assertEqual(job.status, PublicRewardSyncJob.Status.SUCCEEDED)
        snapshot = PublicRewardSnapshot.objects.get(manager_slug="blasco93")
        self.assertEqual(snapshot.movements[0]["id"], "reward-1")

    def test_reward_manager_selector_uses_cached_public_rewards(self):
        PublicRewardSnapshot.objects.create(
            manager_slug="blasco93", manager_nickname="Blasco93", source_version=1,
            movements=[{
                "id": "blasco-reward", "occurred_at": "2026-08-18T20:00:00Z",
                "direction": "reward", "cash_direction": "reward", "category": "reward",
                "market": "GW5 · Champ. – Rare · Puesto 66", "reward_type": "essence",
                "reward_rarity": "rare", "essence": [{"quantity": 510, "rarity": "rare"}],
                "essence_quantity": 510, "cards": [], "gross_eur": 0, "eth": 0, "currency": "",
            }],
        )

        response = self.client.get(reverse("movements"), {"category": "reward", "manager": "blasco93"})

        self.assertEqual(response.context["selected_manager"], "blasco93")
        self.assertContains(response, "Recompensas de Blasco93")
        self.assertContains(response, "+510 Esencia")
        self.assertContains(response, '<option value="blasco93" selected>Blasco93</option>', html=True)

    def test_first_public_reward_visit_enqueues_background_sync(self):
        response = self.client.get(reverse("movements"), {"category": "reward", "manager": "blasco93"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(PublicRewardSyncJob.objects.filter(manager_slug="blasco93", status="queued").exists())
        self.assertContains(response, "Cargando las recompensas de Blasco93")

    def test_reward_filter_separates_money_essence_and_cards(self):
        base = {
            "occurred_at": "2026-08-18T20:00:00Z", "direction": "reward",
            "cash_direction": "reward", "market": "GW5", "category": "reward",
            "gross_eur": 0, "eth": 0, "currency": "", "cards": [],
            "essence": [], "essence_quantity": 0,
        }
        money = dict(base, id="money", reward_type="money", gross_eur=22.46, currency="ETH")
        essence = dict(base, id="essence", reward_type="essence", reward_rarity="super_rare",
                       essence=[{"quantity": 10, "rarity": "super_rare"}], essence_quantity=10)
        card_data = {"asset_id": "reward-card", "player": "Carta premio", "team": "Equipo", "rarity": "limited"}
        card = dict(base, id="card", reward_type="card", reward_rarity="limited", cards=[card_data])
        MovementSnapshot.objects.create(user=self.user, movements=[money, essence, card], source_version=14)

        response = self.client.get(reverse("movements"), {"category": "reward", "reward_type": "essence"})

        self.assertEqual(response.context["total_rows"], 1)
        self.assertContains(response, "+10 Esencia")
        self.assertContains(response, "rarity-super_rare")
        self.assertNotContains(response, "22,46 €")
        self.assertNotContains(response, "Carta premio")

    def test_reward_kpis_sum_money_essence_by_rarity_and_cards(self):
        base = {
            "occurred_at": "2026-08-18T20:00:00Z", "direction": "reward",
            "cash_direction": "reward", "market": "GW5", "category": "reward",
            "gross_eur": 0, "eth": 0, "currency": "", "cards": [],
            "essence": [], "essence_quantity": 0,
        }
        limited = dict(base, id="limited", reward_type="essence", reward_rarity="limited",
                       essence=[{"quantity": 12, "rarity": "limited"}], essence_quantity=12)
        rare = dict(base, id="rare", reward_type="essence", reward_rarity="rare",
                    essence=[{"quantity": 20, "rarity": "rare"}], essence_quantity=20)
        super_rare = dict(base, id="super-rare", reward_type="essence", reward_rarity="super_rare",
                          essence=[{"quantity": 4, "rarity": "super_rare"}], essence_quantity=4)
        money = dict(base, id="money", reward_type="money", gross_eur=22.46, currency="ETH")
        cards = dict(base, id="cards", reward_type="card", cards=[{"player": "Uno"}, {"player": "Dos"}])
        MovementSnapshot.objects.create(
            user=self.user, source_version=14,
            movements=[limited, rare, super_rare, money, cards],
        )

        response = self.client.get(reverse("movements"), {"category": "reward"})

        self.assertEqual(response.context["totals"]["reward_money"], Decimal("22.46"))
        self.assertEqual(response.context["totals"]["essence_limited"], 12)
        self.assertEqual(response.context["totals"]["essence_rare"], 20)
        self.assertEqual(response.context["totals"]["essence_super_rare"], 4)
        self.assertEqual(response.context["totals"]["reward_cards"], 2)
        self.assertContains(response, "Dinero recibido")
        self.assertContains(response, "Esencia recibida")
        self.assertContains(response, "Cartas recibidas")

    def test_default_history_starts_on_current_season_date_but_can_be_cleared(self):
        MovementSnapshot.objects.create(user=self.user, source_version=14, movements=[
            {"id": "before", "occurred_at": "2026-08-11T21:59:00Z", "direction": "purchase", "category": "other", "cards": [{"player": "Antes de temporada"}]},
            {"id": "current", "occurred_at": "2026-08-11T22:00:00Z", "direction": "purchase", "category": "other", "cards": [{"player": "Temporada actual"}]},
        ])

        default_response = self.client.get(reverse("movements"), {"category": "other"})
        all_response = self.client.get(reverse("movements"), {"category": "other", "date_from": ""})

        self.assertEqual(default_response.context["date_from"], "2026-08-12")
        self.assertNotContains(default_response, "Antes de temporada")
        self.assertContains(default_response, "Temporada actual")
        self.assertContains(all_response, "Antes de temporada")

    def test_date_from_is_the_opening_position_and_rewards_stay_separate(self):
        carvalho = {"asset_id": "carvalho", "player": "Vítor Carvalho", "rarity": "rare", "in_season": False}
        espino = {"asset_id": "espino", "player": "Alfonso Espino", "rarity": "super_rare", "in_season": False}
        moncayola = {"asset_id": "moncayola", "player": "Moncayola", "rarity": "super_rare", "in_season": False}
        MovementSnapshot.objects.create(user=self.user, source_version=14, movements=[
            {"id": "carvalho-buy", "occurred_at": "2026-01-29T18:20:00Z", "direction": "purchase", "cash_direction": "purchase", "category": "other", "cards": [carvalho], "received_cards": [carvalho], "gross_eur": 2.36, "net_eur": 0, "credits_eur": 2.36},
            {"id": "carvalho-trade", "occurred_at": "2026-05-27T11:13:00Z", "direction": "trade", "cash_direction": "other", "category": "other", "cards": [carvalho, espino], "sent_cards": [carvalho], "received_cards": [espino], "gross_eur": 0, "net_eur": 0},
            {"id": "espino-trade", "occurred_at": "2026-08-19T09:57:00Z", "direction": "trade", "cash_direction": "other", "category": "other", "cards": [espino, moncayola], "sent_cards": [espino], "received_cards": [moncayola], "gross_eur": 0, "net_eur": 0},
            {"id": "reward", "occurred_at": "2026-08-20T18:00:00Z", "direction": "reward", "category": "reward", "reward_type": "money", "gross_eur": 10, "net_eur": 10, "cards": []},
        ])

        response = self.client.get(reverse("movements"), {"category": "all"})

        self.assertEqual(response.context["total_rows"], 1)
        self.assertEqual(response.context["totals"]["purchase_count"], 0)
        self.assertEqual(response.context["totals"]["trade_count"], 1)
        self.assertContains(response, "Alfonso Espino")
        self.assertContains(response, "Moncayola")
        self.assertNotContains(response, "Vítor Carvalho")
        self.assertNotContains(response, "Premio recibido")
        self.assertNotContains(response, "Premio monetario")

        reward_response = self.client.get(reverse("movements"), {"category": "reward"})
        self.assertContains(reward_response, "Premio recibido")
        self.assertContains(reward_response, "Premio monetario")

    def test_page_filters_category_rarity_and_calculates_totals(self):
        MovementSnapshot.objects.create(user=self.user, refreshed_at=datetime.now(tz=ZoneInfo("Europe/Madrid")), movements=[
            {"id": "buy", "occurred_at": "2026-08-18T10:00:00Z", "direction": "purchase", "market": "Subasta", "category": "laliga_inseason", "cards": [{"player": "Carles Aleñá", "rarity": "rare", "in_season": True}], "gross_eur": 10, "net_eur": 8, "credits_eur": 2, "fee_eur": 0, "currency": "EUR", "eth": 0},
            {"id": "other", "occurred_at": "2026-08-17T10:00:00Z", "direction": "purchase", "market": "Subasta", "category": "other", "cards": [{"player": "Take", "rarity": "limited", "in_season": False}], "gross_eur": 6.5, "net_eur": 6.5, "credits_eur": 0, "fee_eur": 0, "currency": "ETH", "eth": 0.0039},
        ])
        response = self.client.get(reverse("movements"), {"category": "laliga_inseason", "rarity": "rare"})
        self.assertContains(response, "Carles Aleñá")
        self.assertNotContains(response, ">Take<")
        self.assertEqual(response.context["totals"]["purchases"], Decimal("10"))
        self.assertEqual(response.context["totals"]["purchases_inseason"], Decimal("10"))
        self.assertEqual(response.context["totals"]["purchases_classic"], Decimal("0"))
        self.assertContains(response, "season-mark is-inseason")
        self.assertNotContains(response, "Créditos usados")
        self.assertNotContains(response, "2,00 € créditos")

        inseason_response = self.client.get(reverse("movements"), {
            "category": "all", "seasonality": "inseason", "date_from": "",
        })
        self.assertContains(inseason_response, "Carles Aleñá")
        self.assertNotContains(inseason_response, ">Take<")
        self.assertEqual(inseason_response.context["selected_seasonality"], "inseason")

        classic_response = self.client.get(reverse("movements"), {
            "category": "all", "seasonality": "classic", "date_from": "",
        })
        self.assertContains(classic_response, "Take")
        self.assertNotContains(classic_response, "Carles Aleñá")
        self.assertNotContains(classic_response, "name=\"player\"")
        self.assertContains(classic_response, "0,0039 ETH")
        self.assertNotContains(classic_response, "0,003900 ETH")

    def test_rarity_filter_hides_common_and_custom_series(self):
        MovementSnapshot.objects.create(user=self.user, movements=[
            {"id": "rare", "occurred_at": "2026-08-18T10:00:00Z", "direction": "purchase", "category": "other", "cards": [{"player": "Rare", "rarity": "rare", "in_season": True}]},
            {"id": "common", "occurred_at": "2026-08-18T09:00:00Z", "direction": "reward", "category": "reward", "cards": [{"player": "Common", "rarity": "common", "in_season": True}]},
            {"id": "custom", "occurred_at": "2026-08-18T08:00:00Z", "direction": "reward", "category": "reward", "cards": [{"player": "Custom", "rarity": "custom_series", "in_season": False}]},
        ])

        response = self.client.get(reverse("movements"), {"category": "all"})

        self.assertEqual(response.context["available_rarities"], ["rare"])
        self.assertNotContains(response, 'value="common"')
        self.assertNotContains(response, 'value="custom_series"')

    def test_default_page_includes_classic_purchases_and_rarity_indicator(self):
        camavinga = {
            "asset_id": "camavinga-73", "player": "Eduardo Camavinga", "player_slug": "eduardo-camavinga",
            "team": "Real Madrid", "rarity": "rare", "season_year": 2021, "in_season": False,
            "is_laliga": True, "serial_number": 73,
        }
        MovementSnapshot.objects.create(user=self.user, source_version=14, movements=[{
            "id": "camavinga-buy", "occurred_at": "2026-08-23T08:19:25Z", "direction": "purchase",
            "cash_direction": "purchase", "market": "Oferta pública", "category": "laliga_inseason",
            "cards": [camavinga], "received_cards": [camavinga], "sent_cards": [],
            "gross_eur": 5.52, "net_eur": 5.52, "currency": "EUR",
        }])

        response = self.client.get(reverse("movements"))

        self.assertEqual(response.context["selected_category"], "all")
        self.assertContains(response, "Eduardo Camavinga")
        self.assertContains(response, "Classic")
        self.assertContains(response, "card-rarity-dot rarity-rare")
        self.assertContains(response, "season-mark is-classic")
        self.assertEqual(response.context["totals"]["purchases_classic"], Decimal("5.52"))
        self.assertEqual(response.context["totals"]["purchases_inseason"], Decimal("0"))

        laliga_response = self.client.get(reverse("movements"), {"category": "laliga_inseason"})
        self.assertContains(laliga_response, "Eduardo Camavinga")

    def test_page_inlines_purchase_sale_cycles_and_keeps_unmatched_movements(self):
        mathew = {
            "asset_id": "mathew-11", "player": "Mathew Ryan", "player_slug": "mathew-ryan",
            "team": "Levante UD", "rarity": "rare", "season_year": 2026,
            "in_season": True, "serial_number": 11,
        }
        zakaria_bought = {
            "asset_id": "zakaria-16", "player": "Zakaria Eddahchouri", "player_slug": "zakaria",
            "team": "RC Deportivo", "rarity": "rare", "season_year": 2026,
            "in_season": True, "serial_number": 16,
        }
        zakaria_sold = dict(zakaria_bought, asset_id="zakaria-10", serial_number=10)
        MovementSnapshot.objects.create(user=self.user, movements=[
            {"id": "mathew-buy", "occurred_at": "2026-08-14T10:35:00Z", "direction": "purchase", "cash_direction": "purchase", "market": "Subasta", "category": "laliga_inseason", "cards": [mathew], "received_cards": [mathew], "sent_cards": [], "gross_eur": 71.76, "net_eur": 71.76, "credits_eur": 0},
            {"id": "mathew-sale", "occurred_at": "2026-08-20T07:01:00Z", "direction": "sale", "cash_direction": "sale", "market": "Compra instantánea", "category": "laliga_inseason", "cards": [mathew], "received_cards": [], "sent_cards": [mathew], "gross_eur": 83, "net_eur": 78.85, "fee_eur": 4.15},
            {"id": "zakaria-buy", "occurred_at": "2026-08-10T10:00:00Z", "direction": "purchase", "cash_direction": "purchase", "market": "Subasta", "category": "laliga_inseason", "cards": [zakaria_bought], "received_cards": [zakaria_bought], "sent_cards": [], "gross_eur": 5.4, "net_eur": 5.4},
            {"id": "zakaria-sale", "occurred_at": "2026-08-20T02:07:00Z", "direction": "sale", "cash_direction": "sale", "market": "Oferta directa", "category": "laliga_inseason", "cards": [zakaria_sold], "received_cards": [], "sent_cards": [zakaria_sold], "gross_eur": 7.56, "net_eur": 7.56, "fee_eur": 0},
            {"id": "unmatched", "occurred_at": "2026-08-19T12:00:00Z", "direction": "purchase", "cash_direction": "purchase", "market": "Subasta", "category": "laliga_inseason", "cards": [{"asset_id": "unmatched", "player": "Jugador sin vender", "player_slug": "sin-vender", "team": "Equipo", "rarity": "rare", "season_year": 2026, "in_season": True, "serial_number": 20}], "received_cards": [{"asset_id": "unmatched", "player": "Jugador sin vender", "player_slug": "sin-vender", "team": "Equipo", "rarity": "rare", "season_year": 2026, "in_season": True, "serial_number": 20}], "sent_cards": [], "gross_eur": 6, "net_eur": 6},
        ])

        response = self.client.get(reverse("movements"), {"category": "laliga_inseason", "date_from": ""})

        self.assertContains(response, "3 movimientos")
        self.assertContains(response, 'class="movement-grouped-row"', count=2)
        self.assertContains(response, "Misma carta")
        self.assertContains(response, "+7,09 €")
        self.assertContains(response, "Cartas distintas: compra #16 · venta #10")
        self.assertContains(response, "Jugador sin vender")
        self.assertNotContains(response, "Compraventas agrupadas")

    def test_cash_and_card_trade_shows_gross_net_and_commission(self):
        sivera = {"asset_id": "sivera-10", "player": "Sivera", "team": "D. Alavés", "rarity": "rare", "serial_number": 10}
        dituro = {"asset_id": "dituro-14", "player": "Matías Dituro", "team": "Elche CF", "rarity": "rare", "serial_number": 14}
        MovementSnapshot.objects.create(user=self.user, movements=[{
            "id": "swap", "occurred_at": "2026-08-19T11:02:00Z", "direction": "trade",
            "cash_direction": "sale", "market": "Intercambio + dinero", "category": "laliga_inseason",
            "cards": [sivera, dituro], "sent_cards": [sivera], "received_cards": [dituro],
            "gross_eur": 45, "net_eur": 42.75, "fee_eur": 2.25, "credits_eur": 0,
            "currency": "EUR", "eth": 0,
        }])

        response = self.client.get(reverse("movements"), {"category": "laliga_inseason"})

        self.assertContains(response, "Importe")
        self.assertContains(response, "45,00 €")
        self.assertContains(response, "42,75 €")
        self.assertContains(response, "−2,25 € comisión")

    def test_yangel_chain_is_one_row_and_summary_includes_later_card_sale(self):
        yangel = {
            "asset_id": "yangel-23", "player": "Yangel Herrera", "player_slug": "yangel-herrera",
            "team": "Girona FC", "rarity": "rare", "season_year": 2026, "in_season": True,
            "is_laliga": True, "serial_number": 23,
        }
        hugo = {
            "asset_id": "hugo-17", "player": "Hugo Cuenca", "player_slug": "hugo-cuenca",
            "team": "Genoa", "rarity": "rare", "season_year": 2026, "in_season": True,
            "is_laliga": False, "serial_number": 17,
        }
        ruben = {
            "asset_id": "ruben-110", "player": "Rubén Sánchez", "player_slug": "ruben-sanchez",
            "team": "Espanyol", "rarity": "limited", "season_year": 2026, "in_season": True,
            "is_laliga": True, "serial_number": 110,
        }
        MovementSnapshot.objects.create(user=self.user, source_version=14, movements=[
            {"id": "yangel-buy", "occurred_at": "2026-08-20T20:21:25Z", "direction": "purchase", "cash_direction": "purchase", "market": "Subasta", "category": "laliga_inseason", "cards": [yangel], "received_cards": [yangel], "sent_cards": [], "gross_eur": 20, "net_eur": 20},
            {"id": "yangel-trade", "occurred_at": "2026-08-20T21:44:44Z", "direction": "trade", "cash_direction": "sale", "market": "Intercambio + dinero", "category": "laliga_inseason", "cards": [yangel, hugo, ruben], "received_cards": [hugo, ruben], "sent_cards": [yangel], "gross_eur": 4.48, "net_eur": 4.26, "fee_eur": .22},
            {"id": "hugo-sale", "occurred_at": "2026-08-22T10:00:00Z", "direction": "sale", "cash_direction": "sale", "market": "Oferta pública", "category": "other", "cards": [hugo], "received_cards": [], "sent_cards": [hugo], "gross_eur": 3.2, "net_eur": 3.04, "fee_eur": .16},
            {"id": "unrelated", "occurred_at": "2026-08-21T10:00:00Z", "direction": "purchase", "cash_direction": "purchase", "market": "Subasta", "category": "other", "cards": [{"asset_id": "other", "player": "Otro jugador", "rarity": "limited"}], "received_cards": [{"asset_id": "other", "player": "Otro jugador", "rarity": "limited"}], "sent_cards": [], "gross_eur": 100, "net_eur": 100},
        ])

        response = self.client.get(reverse("movements"), {
            "category": "laliga_inseason", "date_from": "",
        })

        self.assertEqual(response.context["total_rows"], 1)
        self.assertContains(response, "Yangel Herrera")
        self.assertContains(response, "Hugo Cuenca")
        self.assertContains(response, "Rubén Sánchez")
        self.assertContains(response, "Ventas posteriores")
        self.assertContains(response, "+7,30 €")
        self.assertContains(response, "Balance actual")
        self.assertNotContains(response, "€ netos")
        self.assertNotContains(response, "Otro jugador")
        self.assertEqual(response.context["totals"]["purchases"], Decimal("20"))
        self.assertEqual(response.context["totals"]["trade_cash_in"], Decimal("4.26"))
        self.assertEqual(response.context["totals"]["sales_net"], Decimal("7.30"))
        self.assertEqual(response.context["totals"]["sales_inseason"], Decimal("7.30"))
        self.assertEqual(response.context["totals"]["sales_classic"], Decimal("0"))
        self.assertEqual(response.context["totals"]["balance"], Decimal("-12.70"))

        classic_only = self.client.get(reverse("movements"), {
            "category": "laliga_inseason", "seasonality": "classic", "date_from": "",
        })
        self.assertNotContains(classic_only, "Yangel Herrera")
        self.assertEqual(classic_only.context["total_rows"], 0)

    def test_trade_type_follows_the_card_bought_or_sold_not_the_payment_cards(self):
        juan = {
            "asset_id": "juan-is", "player": "Juan Iglesias", "rarity": "rare",
            "season_year": 2026, "in_season": True,
        }
        lejeune = {
            "asset_id": "lejeune-classic", "player": "Florian Lejeune", "rarity": "limited",
            "season_year": 2024, "in_season": False,
        }
        classic_target = {
            "asset_id": "classic-target", "player": "Objetivo Classic", "rarity": "rare",
            "season_year": 2023, "in_season": False,
        }
        inseason_payment = {
            "asset_id": "is-payment", "player": "Pago In-Season", "rarity": "limited",
            "season_year": 2026, "in_season": True,
        }
        MovementSnapshot.objects.create(user=self.user, source_version=14, movements=[
            {
                "id": "juan-sale", "occurred_at": "2026-08-23T10:00:00Z",
                "direction": "trade", "cash_direction": "sale", "market": "Intercambio + dinero",
                "category": "laliga_inseason", "cards": [juan, lejeune],
                "sent_cards": [juan], "received_cards": [lejeune],
                "gross_eur": 16.76, "net_eur": 15.92, "fee_eur": .84,
            },
            {
                "id": "classic-buy", "occurred_at": "2026-08-23T11:00:00Z",
                "direction": "trade", "cash_direction": "purchase", "market": "Intercambio + dinero",
                "category": "other", "cards": [classic_target, inseason_payment],
                "sent_cards": [inseason_payment], "received_cards": [classic_target],
                "gross_eur": 5, "net_eur": 5, "fee_eur": 0,
            },
        ])

        response = self.client.get(reverse("movements"), {"category": "all", "date_from": ""})
        self.assertEqual(response.context["totals"]["sales_inseason"], Decimal("15.92"))
        self.assertEqual(response.context["totals"]["sales_classic"], Decimal("0"))
        self.assertEqual(response.context["totals"]["purchases_inseason"], Decimal("0"))
        self.assertEqual(response.context["totals"]["purchases_classic"], Decimal("5"))

        analytics = self.client.get(reverse("movement_analytics"), {"date_from": "2026-08-12"})
        self.assertEqual(analytics.status_code, 200)
        by_type = {row["key"]: row for row in analytics.context["breakdown"]}
        self.assertEqual(by_type["inseason"]["received"], Decimal("15.92"))
        self.assertEqual(by_type["classic"]["spent"], Decimal("5"))
        self.assertEqual(analytics.context["timeline"][0]["balance"], Decimal("10.92"))
        self.assertContains(analytics, "Capital aún invertido")
        self.assertContains(analytics, "Beneficio realizado")

        classic_only = self.client.get(reverse("movements"), {
            "category": "all", "seasonality": "classic", "date_from": "",
        })
        self.assertNotContains(classic_only, "Juan Iglesias")
        self.assertContains(classic_only, "Objetivo Classic")

    def test_first_visit_enqueues_background_sync(self):
        response = self.client.get(reverse("movements"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(MovementSyncJob.objects.filter(user=self.user, status="queued").exists())

    def test_old_provisional_snapshot_is_hidden_and_rebuilt(self):
        MovementSnapshot.objects.create(
            user=self.user, source_version=1,
            movements=[{"id": "live-bid", "category": "laliga_inseason", "cards": [{"player": "Puja abierta"}]}],
        )
        response = self.client.get(reverse("movements"))
        self.assertNotContains(response, "Puja abierta")
        self.assertContains(response, "Cargando tu historial")
        self.assertTrue(MovementSyncJob.objects.filter(user=self.user, status="queued").exists())


class OpportunityMarketTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="opportunity-user", password="test-pass")
        self.client.force_login(self.user)

    def test_robust_reference_rejects_extreme_sale_and_prioritizes_recent_data(self):
        summary = robust_sales_reference([
            {"eur": 10, "date": "2026-08-25T10:00:00Z"},
            {"eur": 11, "date": "2026-08-24T10:00:00Z"},
            {"eur": 12, "date": "2026-08-23T10:00:00Z"},
            {"eur": 11.5, "date": "2026-08-22T10:00:00Z"},
            {"eur": 95, "date": "2026-08-21T10:00:00Z"},
        ], now=datetime(2026, 8, 26, tzinfo=ZoneInfo("UTC")))

        self.assertLess(summary["value"], 13)
        self.assertEqual(len(summary["sales"]), 4)
        self.assertIn(summary["confidence"], {"medium", "high"})

    def test_only_completed_public_offers_are_comparable(self):
        self.assertEqual(
            _comparable_kind({"deal": {"__typename": "TokenOffer", "type": "SINGLE_BUY_OFFER"}}),
            ("public", "Oferta pública"),
        )
        self.assertEqual(_comparable_kind({"deal": {"__typename": "TokenAuction"}}), (None, None))
        self.assertEqual(_comparable_kind({"deal": {"__typename": "TokenPrimaryOffer"}}), (None, None))

    def test_floor_query_uses_only_one_player_root_field(self):
        query, variables = _floor_query({"player_slug": "player-one"})

        self.assertEqual(query.count("anyPlayer("), 1)
        self.assertEqual(query.count("anyCards("), 2)
        self.assertIn("gbpCents", query)
        self.assertIn("usdCents", query)
        self.assertEqual(variables, {"slug": "player-one"})

    def test_history_query_respects_sorare_twenty_item_limit(self):
        query, variables = _history_query(["player-one", "player-two"])

        self.assertNotIn("first: 30", query)
        self.assertEqual(query.count("first: 20"), 4)
        self.assertIn("gbpCents", query)
        self.assertIn("usdCents", query)
        self.assertEqual(variables["slug0"], "player-one")

    def test_sales_can_estimate_value_without_an_active_floor(self):
        rows, _ = build_opportunity_rows([{
            "player": "Jugador sin suelo", "player_slug": "jugador-sin-suelo",
            "limited": {"sales": [
                {"eur": 10, "date": "2026-08-25T10:00:00Z", "kind": "public", "label": "Oferta pública"},
                {"eur": 12, "date": "2026-08-24T10:00:00Z", "kind": "public", "label": "Oferta pública"},
            ]},
            "rare": {"sales": []},
        }])

        self.assertIsNone(rows[0]["limited"].get("floor"))
        self.assertEqual(rows[0]["limited"]["market_value"], rows[0]["limited"]["sales_reference"])
        self.assertIsNone(rows[0]["recommended_rarity"])

    def test_market_value_is_capped_by_floor_and_cross_rarity_detects_bargain(self):
        sales = lambda values: [
            {"eur": value, "date": f"2026-08-{20 + index:02d}T10:00:00Z", "kind": "public", "label": "Oferta pública"}
            for index, value in enumerate(values)
        ]
        players = [{
            "player": "Jugador ganga", "player_slug": "jugador-ganga",
            "limited": {"floor": 20, "sales": sales([18, 20, 19, 21, 20])},
            "rare": {"floor": 38, "sales": sales([70, 75, 72, 74, 71])},
        }]

        rows, metadata = build_opportunity_rows(players)

        self.assertEqual(rows[0]["limited"]["market_value"], 20)
        self.assertEqual(rows[0]["rare"]["market_value"], 38)
        self.assertEqual(metadata["ratio_source"], "fallback")
        self.assertEqual(rows[0]["recommended_rarity"], "rare")
        self.assertGreater(rows[0]["discount_percent"], 20)

    def test_page_filters_cached_opportunities_without_calling_sorare(self):
        OpportunitySnapshot.objects.create(
            rows=[{
                "player": "Oportunidad Roja", "player_slug": "oportunidad-roja",
                "team": "Real Madrid", "position": "Forward", "recommended_rarity": "rare",
                "discount_percent": 31.5, "confidence": "high", "player_picture_url": "",
                "team_picture_url": "",
                "limited": {"floor": 5, "market_value": 5, "sales_reference": 6, "sales": [], "parity_reference": 5, "reference_value": 5},
                "rare": {"floor": 18, "market_value": 18, "sales_reference": 25, "sales": [], "parity_reference": 22.5, "reference_value": 24},
            }, {
                "player": "Sin señal", "player_slug": "sin-senal", "team": "Sevilla FC",
                "position": "Defender", "recommended_rarity": None, "discount_percent": 0,
                "confidence": "low", "limited": {}, "rare": {},
            }],
            metadata={"rare_limited_ratio": 4.4, "ratio_source": "learned", "ratio_sample": 20, "players_analyzed": 2, "active_listings": 3, "opportunities": 1},
        )

        response = self.client.get(reverse("opportunities"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.per_page, 50)
        self.assertContains(response, "Oportunidad Roja")
        self.assertContains(response, "Sin señal")
        self.assertContains(response, "Las subastas no se incluyen")
        self.assertContains(response, "compras instantáneas")
        self.assertNotContains(response, "Buscar jugador")
        self.assertNotContains(response, "Descuento mínimo</span>")
        self.assertNotContains(response, "Por página")

        signals_only = self.client.get(reverse("opportunities"), {"signals_only": "1"})
        self.assertNotContains(signals_only, ">Sin señal<")

    def test_team_selector_uses_club_shields_and_accessible_names(self):
        OpportunitySnapshot.objects.create(
            rows=[{
                "player": "Jugador", "player_slug": "jugador", "team": "Real Club Deportivo de La Coruña",
                "team_slug": "deportivo-la-coruna-a-coruna", "team_picture_url": "",
                "position": "Forward", "recommended_rarity": None, "discount_percent": 0,
                "confidence": "low", "limited": {}, "rare": {},
            }],
            metadata={"team_catalog": [{
                "slug": "deportivo-la-coruna-a-coruna", "name": "Real Club Deportivo de La Coruña",
            }]},
        )

        response = self.client.get(reverse("opportunities"))

        self.assertContains(response, "deportivo-la-coruna-logo-png_seeklogo-187816.png")
        self.assertContains(response, 'title="Real Club Deportivo de La Coruña"')

    @patch("web_services.opportunity_market.collect_opportunity_market")
    def test_worker_persists_snapshot_and_finishes_job(self, collect):
        collect.return_value = {
            "rows": [{"player": "Prueba"}],
            "metadata": {"players_analyzed": 1, "opportunities": 1},
        }
        job = OpportunityRefreshJob.objects.create(user=self.user, target_team_slugs=["real-madrid-madrid"])

        processed = process_next_opportunity_refresh()

        job.refresh_from_db()
        self.assertEqual(processed.pk, job.pk)
        self.assertEqual(job.status, OpportunityRefreshJob.Status.SUCCEEDED)
        self.assertEqual(job.player_count, 1)
        self.assertEqual(OpportunitySnapshot.objects.get().rows[0]["player"], "Prueba")
        self.assertEqual(collect.call_args.kwargs["team_slugs"], ["real-madrid-madrid"])

    def test_first_visit_waits_for_team_selection(self):
        response = self.client.get(reverse("opportunities"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OpportunityRefreshJob.objects.filter(status="queued").exists())
        self.assertContains(response, "Elige por dónde empezar")

    def test_refresh_can_be_enqueued_for_selected_teams(self):
        response = self.client.post(
            reverse("enqueue_opportunity_refresh"),
            data=json.dumps({"teams": ["real-madrid-madrid", "barcelona-barcelona"]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        job = OpportunityRefreshJob.objects.get(pk=response.json()["job_id"])
        self.assertEqual(job.target_team_slugs, ["real-madrid-madrid", "barcelona-barcelona"])

    @patch("web_services.opportunity_market.collect_opportunity_market")
    def test_selected_team_refresh_preserves_other_teams(self, collect):
        OpportunitySnapshot.objects.create(rows=[
            {"player": "Antiguo Madrid", "player_slug": "old-madrid", "team_slug": "real-madrid-madrid", "limited": {}, "rare": {}},
            {"player": "Jugador Barça", "player_slug": "barca-player", "team_slug": "barcelona-barcelona", "limited": {}, "rare": {}},
        ])
        collect.return_value = {
            "rows": [{"player": "Nuevo Madrid", "player_slug": "new-madrid", "team_slug": "real-madrid-madrid", "limited": {}, "rare": {}}],
            "metadata": {
                "players_analyzed": 1, "opportunities": 0,
                "refreshed_team_slugs": ["real-madrid-madrid"],
                "team_catalog": [
                    {"slug": "real-madrid-madrid", "name": "Real Madrid"},
                    {"slug": "barcelona-barcelona", "name": "FC Barcelona"},
                ],
            },
        }
        OpportunityRefreshJob.objects.create(user=self.user, target_team_slugs=["real-madrid-madrid"])

        process_next_opportunity_refresh()

        players = {row["player"] for row in OpportunitySnapshot.objects.get().rows}
        self.assertEqual(players, {"Nuevo Madrid", "Jugador Barça"})
