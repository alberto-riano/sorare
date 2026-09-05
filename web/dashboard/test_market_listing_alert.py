import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

import market_listing_alert
from dashboard.forms import TelegramSettingsForm
from web_services.process_runner import run_market_listing_alert
from web_services.config_files import SorarePaths


NOW = datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("UTC"))


def card(rarity="rare", *, slug="mbappe-2026-rare-1"):
    return {
        "assetId": f"asset-{rarity}", "slug": slug, "rarityTyped": rarity,
        "seasonYear": 2026, "serialNumber": 1, "inSeasonEligible": True,
        "anyPlayer": {"slug": "kylian-mbappe", "displayName": "Kylian Mbappé", "squaredPictureUrl": "player.png"},
        "anyTeam": {"name": "Real Madrid"},
    }


def offer(offer_id="offer-new", rarity="rare", price=60300):
    return {
        "id": offer_id, "receiverSide": {"amounts": {"eurCents": price}},
        "senderSide": {"anyCards": [card(rarity)]},
    }


def history(price, typename="TokenAuction", offer_type=None):
    deal = {"__typename": typename}
    if offer_type:
        deal["type"] = offer_type
    return {
        "amounts": {"eurCents": price}, "date": "2026-09-04T10:00:00Z",
        "card": {"seasonYear": 2026, "inSeasonEligible": True}, "deal": deal,
    }


class MarketListingAlertTests(SimpleTestCase):
    def test_alert_configuration_is_adjustable(self):
        form = TelegramSettingsForm({
            "auction_alert_minutes": 3, "auction_alert_min_saving_percent": 20,
            "auction_alert_rarities": ["rare"],
            "market_alert_min_saving_percent": "27.5",
            "market_alert_min_limited_value_eur": "2.25",
            "market_alert_min_comparables": "3",
            "notify_mode": "all", "notify_drop_eur": "1", "rarity": "rare",
            "classic_players": "Jugador 10", "in_season_players": "",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(str(form.cleaned_data["market_alert_min_saving_percent"]), "27.5")
        self.assertEqual(form.cleaned_data["market_alert_min_comparables"], 3)

    @patch("web_services.process_runner._run_command")
    def test_runner_uses_market_listing_script(self, run_command):
        paths = SorarePaths(repo_root=Path(__file__).resolve().parents[2])
        run_market_listing_alert(paths, dry_run=True)
        command = run_command.call_args.args[0]
        self.assertTrue(command[1].endswith("market_listing_alert.py"))
        self.assertEqual(command[-1], "--dry-run")

    def test_only_laliga_rare_inseason_candidates_are_kept(self):
        roster = {"kylian-mbappe": {}}
        self.assertTrue(market_listing_alert._matching_listing(offer(), roster))
        self.assertFalse(market_listing_alert._matching_listing(offer(rarity="limited"), roster))
        self.assertFalse(market_listing_alert._matching_listing(offer(), {"other-player": {}}))

    def test_history_uses_auctions_and_public_offers_but_not_instant_sales(self):
        prices = [
            history(85000),
            history(90000, "TokenOffer", "SINGLE_BUY_OFFER"),
            history(60000, "TokenOffer", "SINGLE_SALE_OFFER"),
        ]
        rows = market_listing_alert._comparable_sales(prices, (1, 1, 2000), NOW)
        self.assertEqual([row["eur"] for row in rows], [850.0, 900.0])

    @patch("market_listing_alert.graphql_request")
    @patch("market_listing_alert.get_live_single_sale_offers")
    def test_fair_value_excludes_new_listing_from_floor(self, live_offers, graphql):
        live_offers.return_value = [
            offer("offer-new", "rare", 60300), offer("rare-peer", "rare", 100000),
            offer("limited-floor", "limited", 25000),
        ]
        graphql.return_value = {"tokens": {
            "rare": [history(85000), history(90000)],
            "limited": [history(24000, "TokenOffer", "SINGLE_BUY_OFFER")],
        }}
        valuation = market_listing_alert._player_valuation(
            offer(), {}, (1, 1, 2000), NOW, ratio=4, ratio_source="learned",
        )
        self.assertEqual(valuation["rare_peer_floor"], 1000)
        self.assertEqual(valuation["limited_value"], 240)
        self.assertGreaterEqual(valuation["fair_value"], 880)
        self.assertLessEqual(valuation["fair_value"], 930)
        saving = (valuation["fair_value"] - 603) / valuation["fair_value"] * 100
        self.assertGreater(saving, 25)

    @patch("market_listing_alert.time.sleep")
    @patch("market_listing_alert._send_telegram")
    @patch("market_listing_alert._player_valuation")
    @patch("market_listing_alert._fetch_recent_listings")
    @patch("market_listing_alert.fetch_exchange_rates", return_value=(1, 1, 2000))
    @patch("market_listing_alert.build_headers", return_value={})
    @patch("market_listing_alert.read_config", return_value={"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"})
    @patch("market_listing_alert._opportunity_context", return_value=({"kylian-mbappe": {"limited": {"market_value": 250}}}, 4, "learned"))
    @patch("market_listing_alert.parse_key_value_file", return_value={
        "MARKET_ALERT_ENABLED": "true", "MARKET_ALERT_MIN_SAVING_PERCENT": "25",
        "MARKET_ALERT_MIN_LIMITED_VALUE_EUR": "1", "MARKET_ALERT_MIN_COMPARABLES": "1",
    })
    def test_run_alerts_once_and_remembers_offer(self, _settings, _context, _config, _headers, _rates, fetch, valuation, send, _sleep):
        fetch.return_value = [offer()]
        valuation.return_value = {
            "fair_value": 900, "limited_value": 250, "rare_sales_count": 2,
            "rare_peer_floor": 1000, "rare_sales_reference": 875, "ratio": 4,
        }
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            with patch.object(market_listing_alert, "STATE_PATH", state_path):
                self.assertEqual(market_listing_alert.run(now=NOW), 0)
                self.assertEqual(market_listing_alert.run(now=NOW), 0)
                state = json.loads(state_path.read_text())
        send.assert_called_once()
        self.assertIn("offer-new", state["seen"])
