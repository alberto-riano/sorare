from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dashboard.models import LineupInventory, LineupRefreshJob
from lineup_helper import matchday_window
from web_services.lineup_assistant import attach_odds, is_laliga_team, propose_lineups, team_code


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
