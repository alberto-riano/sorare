from datetime import date
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dashboard.models import LineupInventory, LineupRefreshJob
from lineup_helper import matchday_window
from web_services.lineup_assistant import attach_odds, fetch_starter_odds, is_laliga_team, propose_lineups, team_code


def card(asset_id, position, average, team="Equipo"):
    return {
        "asset_id": asset_id, "player": asset_id, "position": position, "average": average,
        "team": team, "rarity": "rare", "candidate": True, "in_lineup": False,
        "is_in_season": True, "is_laliga": True,
    }


class LineupAssistantTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="burguis")
        self.client.force_login(self.user)

    def test_refresh_is_enqueued_without_blocking_page(self):
        response = self.client.post(reverse("enqueue_lineup_refresh"))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(LineupRefreshJob.objects.get().status, LineupRefreshJob.Status.QUEUED)

    def test_worker_persists_cards_from_enqueued_refresh(self):
        job = LineupRefreshJob.objects.create(user=self.user)
        rows = [card("gk", "GK", 50)]
        with patch("web_services.lineup_assistant.fetch_lineup_cards", return_value=rows), patch(
            "web_services.lineup_assistant.load_odds", return_value=({}, [], "Sin cuotas disponibles"),
        ):
            from dashboard.management.commands.process_sales_queue import process_next_lineup_refresh
            process_next_lineup_refresh()

        job.refresh_from_db()
        self.assertEqual(job.status, LineupRefreshJob.Status.SUCCEEDED)
        self.assertEqual(LineupInventory.objects.get(user=self.user).cards[0]["asset_id"], "gk")

    def test_proposal_never_reuses_a_card(self):
        cards = [
            *[card(f"gk-{index}", "GK", 45 + index, f"G{index}") for index in range(4)],
            *[card(f"def-{index}", "DEF", 45, f"D{index}") for index in range(8)],
            *[card(f"mid-{index}", "MID", 45, f"M{index}") for index in range(4)],
            *[card(f"fwd-{index}", "FWD", 45, f"F{index}") for index in range(4)],
        ]
        result = propose_lineups(cards)

        self.assertEqual(len(result["lineups"]), 4)
        ids = [player["asset_id"] for lineup in result["lineups"] for player in lineup["cards"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(lineup["total"] <= 260 for lineup in result["lineups"]))

    def test_saved_candidates_do_not_default_to_every_card(self):
        rows = [card("gk", "GK", 50), card("def", "DEF", 42)]
        for row in rows:
            row["candidate"] = False
        LineupInventory.objects.create(user=self.user, cards=rows)

        self.client.post(reverse("lineup_helper"), {
            "action": "save", "candidate_asset_ids": "gk", "average_gk": "55",
        })

        saved = {row["asset_id"]: row for row in LineupInventory.objects.get(user=self.user).cards}
        self.assertTrue(saved["gk"]["candidate"])
        self.assertFalse(saved["def"]["candidate"])
        self.assertEqual(saved["gk"]["average"], 50)

    def test_laliga_filter_uses_domestic_league(self):
        self.assertTrue(is_laliga_team({"name": "Cualquier club", "domesticLeague": {"slug": "laliga-ea-sports"}}))
        self.assertFalse(is_laliga_team({"name": "Crystal Palace FC", "domesticLeague": {"slug": "premier-league"}}))

    def test_alaves_abbreviations_remain_in_the_laliga_pool(self):
        self.assertTrue(is_laliga_team({"name": "D. Alavés", "domesticLeague": {}}))
        self.assertEqual(team_code("D. Alavés"), "ALA")

    def test_matchday_window_keeps_tuesday_short_and_wednesday_switches(self):
        self.assertEqual(matchday_window(date(2026, 9, 15)), (date(2026, 9, 15), date(2026, 9, 17)))
        self.assertEqual(matchday_window(date(2026, 9, 16)), (date(2026, 9, 18), date(2026, 9, 22)))
        self.assertEqual(matchday_window(date(2026, 9, 18)), (date(2026, 9, 18), date(2026, 9, 22)))

    def test_fixture_is_attached_with_own_team_in_bold_position(self):
        row = attach_odds([card("odysseas", "GK", 43, "Sevilla FC")], {
            "Sevilla FC": {"win_prob": .31, "opponent": "Real Club Deportivo de La Coruña", "home": False},
        })[0]
        self.assertEqual(row["fixture_home_code"], "DEP")
        self.assertEqual(row["fixture_away_code"], "SEV")
        self.assertFalse(row["fixture_is_home"])

    def test_a_lineup_uses_at_most_one_classic_card(self):
        rows = [
            card("gk", "GK", 50, "G"), card("def-1", "DEF", 49, "D1"),
            card("def-2", "DEF", 48, "D2"), card("mid", "MID", 47, "M"),
            card("fwd", "FWD", 46, "F"),
        ]
        for row in rows[1:]:
            row["is_in_season"] = False

        result = propose_lineups(rows, count=1)

        self.assertEqual(result["lineups"], [])

    def test_starter_odds_keep_only_players_with_a_percentage(self):
        config = {"config": {"algoliaApplicationId": "APP", "algoliaSearchApiKey": "KEY", "algoliaIndexSuffix": ""}}
        on_sale = Mock()
        on_sale.json.return_value = {"results": [
            {"hits": [{"playing_status_odds": {"starter_odds_basis_points": 8450, "valid_until": 4102444800}}]},
            {"hits": [{"playing_status_odds": None}]},
            {"hits": []},
        ]}
        fallback = Mock()
        fallback.json.return_value = {"results": [
            {"hits": [{"playing_status_odds": None}, {"playing_status_odds": {"starter_odds_basis_points": 3000}}]},
            {"hits": [{"playing_status_odds": {"starter_odds_basis_points": 9000, "valid_until": 1}}]},
        ]}
        with patch("web_services.lineup_assistant.graphql_request", return_value=config), patch(
            "web_services.lineup_assistant.requests.post", side_effect=[on_sale, fallback],
        ) as post:
            odds = fetch_starter_odds(["pedri", "sin-venta", "caducada"], headers={})

        self.assertEqual(odds, {
            "pedri": {"starter_percent": 84, "starter_reliability": ""},
            "sin-venta": {"starter_percent": 30, "starter_reliability": ""},
        })
        first, second = (call.kwargs["json"]["requests"] for call in post.call_args_list)
        self.assertEqual(first[0]["indexName"], "CardsOnSale_StarterOdds")
        self.assertIn("player.slug%3A%22pedri%22", first[0]["params"])
        self.assertEqual([request["indexName"] for request in second], ["BlockchainCard_New"] * 2)

    def test_starter_odds_failure_does_not_break_refresh(self):
        with patch("web_services.lineup_assistant.graphql_request", side_effect=RuntimeError("caído")):
            self.assertEqual(fetch_starter_odds(["pedri"], headers={}), {})
        config = {"config": {"algoliaApplicationId": "APP", "algoliaSearchApiKey": "KEY", "algoliaIndexSuffix": ""}}
        with patch("web_services.lineup_assistant.graphql_request", return_value=config), patch(
            "web_services.lineup_assistant.requests.post", side_effect=RuntimeError("caído"),
        ):
            self.assertEqual(fetch_starter_odds(["pedri"], headers={}), {})

    def test_proposal_shows_starter_percentage(self):
        rows = [
            card("gk", "GK", 50, "G"), card("def-1", "DEF", 49, "D1"),
            card("def-2", "DEF", 48, "D2"), card("mid", "MID", 47, "M"),
            card("fwd", "FWD", 46, "F"),
        ]
        rows[0]["starter_percent"] = 92
        rows[3]["starter_percent"] = 35
        LineupInventory.objects.create(user=self.user, cards=rows)

        response = self.client.post(reverse("lineup_helper"), {
            "action": "generate", "candidate_asset_ids": ",".join(row["asset_id"] for row in rows),
        })

        content = response.content.decode()
        self.assertRegex(content, r'class="starter lh-starter starter-high"[^>]*>92%</b>')
        self.assertRegex(content, r'class="starter lh-starter starter-low"[^>]*>35%</b>')

    def test_clear_removes_saved_candidates(self):
        LineupInventory.objects.create(user=self.user, cards=[card("gk", "GK", 50), card("def", "DEF", 42)])

        self.client.post(reverse("lineup_helper"), {"action": "clear", "candidate_asset_ids": ""})

        self.assertFalse(any(row["candidate"] for row in LineupInventory.objects.get(user=self.user).cards))

    def test_cards_follow_the_players_current_club(self):
        def node(asset_id, active_club):
            return {
                "assetId": asset_id, "slug": asset_id, "rarityTyped": "rare", "inSeasonEligible": True,
                "anyPositions": ["Goalkeeper"], "serialNumber": 1,
                "anyPlayer": {"slug": asset_id, "displayName": asset_id, "averageScore": 50, "activeClub": active_club},
                "anyTeam": {"name": "D. Alavés", "domesticLeague": {"slug": "laliga-es"}},
            }

        page = {"currentUser": {"cards": {"nodes": [
            node("owono", {"name": "Benfica", "domesticLeague": {"slug": "liga-portugal"}}),
            node("libre", None),
            node("fichado", {"name": "Sevilla FC", "domesticLeague": {"slug": "laliga-es"}}),
        ], "pageInfo": {"hasNextPage": False}}, "blockchainCardsInLineups": []}}
        with patch("web_services.lineup_assistant.build_headers", return_value={}), patch(
            "web_services.lineup_assistant.graphql_request", return_value=page,
        ), patch("web_services.lineup_assistant.fetch_starter_odds", return_value={}):
            from web_services.lineup_assistant import fetch_lineup_cards
            cards = fetch_lineup_cards()

        self.assertEqual([(row["asset_id"], row["team"]) for row in cards], [("fichado", "Sevilla FC")])

    def test_starter_filter_limits_the_proposal(self):
        rows = [
            card("gk", "GK", 50, "G"), card("def-1", "DEF", 49, "D1"),
            card("def-2", "DEF", 48, "D2"), card("mid", "MID", 47, "M"),
            card("fwd", "FWD", 46, "F"),
        ]
        for row, percent in zip(rows, (90, 80, 70, 60, 20)):
            row["starter_percent"] = percent
        LineupInventory.objects.create(user=self.user, cards=rows)
        ids = ",".join(row["asset_id"] for row in rows)

        response = self.client.post(reverse("lineup_helper"), {"action": "generate", "candidate_asset_ids": ids, "starter_min": "30"})
        self.assertEqual(response.context["proposal"]["lineups"], [])
        self.assertContains(response, '<option value="30" selected>')
        self.assertContains(response, '<option value="100">')

        response = self.client.post(reverse("lineup_helper"), {"action": "generate", "candidate_asset_ids": ids, "starter_min": "20"})
        self.assertEqual(len(response.context["proposal"]["lineups"]), 1)

    def test_four_lineups_without_repeating_a_player(self):
        def row(asset_id, position, average, team, player=None, in_season=True):
            data = card(asset_id, position, average, team)
            data["player_slug"] = player or asset_id
            data["is_in_season"] = in_season
            return data
        # Pool de la captura de Alberto: la estrategia "la mejor primero"
        # sólo completaba tres alineaciones y repetía a Jon Martín.
        rows = [
            row("valles", "GK", 61, "BET"), row("radu", "GK", 48, "CEL"),
            row("odysseas", "GK", 45, "SEV"), row("herrero", "GK", 43, "MAL"),
            row("alonso", "DEF", 56, "CEL"), row("rudiger", "DEF", 51, "RMA"),
            row("martin-1", "DEF", 46, "RSO", "jon-martin"), row("martin-2", "DEF", 46, "RSO", "jon-martin"),
            row("bellingham", "MID", 72, "RMA"), row("isco", "MID", 51, "BET"),
            row("exposito", "MID", 49, "MAL"), row("cardoso", "MID", 44, "ATM"),
            row("mbappe", "FWD", 71, "RMA", "mbappe"), row("vinicius", "FWD", 53, "RMA"),
            row("cucho", "FWD", 48, "BET"), row("zabiri", "FWD", 46, "RAC"), row("jutgla", "FWD", 42, "CEL"),
            row("mbappe-classic", "FWD", 71, "RMA", "mbappe", False), row("garcia", "DEF", 55, "BAR", in_season=False),
            row("antony-1", "FWD", 54, "BET", "antony", False), row("antony-2", "FWD", 54, "BET", "antony", False),
        ]

        result = propose_lineups(rows)

        self.assertEqual(len(result["lineups"]), 4)
        for lineup in result["lineups"]:
            players = [player["player_slug"] for player in lineup["cards"]]
            self.assertEqual(len(players), len(set(players)))
            self.assertLessEqual(lineup["total"], 260)
            self.assertEqual(sum(bool(player.get("captain")) for player in lineup["cards"]), 1)

    def test_captain_weighs_starting_probability(self):
        rows = [card("gk", "GK", 50, "G"), card("def-1", "DEF", 49, "D1"),
                card("def-2", "DEF", 48, "D2"), card("mid", "MID", 47, "M"), card("fwd", "FWD", 60, "F")]
        for row, percent in zip(rows, (90, 70, 70, 70, 20)):
            row["starter_percent"] = percent

        lineup = propose_lineups(rows, count=1)["lineups"][0]

        self.assertEqual(lineup["captain"]["asset_id"], "gk")
