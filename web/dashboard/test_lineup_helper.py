from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from dashboard.models import LineupInventory
from web_services.lineup_assistant import propose_lineups


def card(asset_id, position, average, team="Equipo"):
    return {
        "asset_id": asset_id, "player": asset_id, "position": position, "average": average,
        "team": team, "rarity": "rare", "excluded": False, "in_lineup": False,
    }


class LineupAssistantTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="burguis")
        self.client.force_login(self.user)

    def test_page_can_refresh_cards_without_excel(self):
        rows = [card("gk", "GK", 50)]
        with patch("dashboard.views.fetch_lineup_cards", return_value=rows), patch(
            "dashboard.views.load_odds", return_value=({}, [], "Sin cuotas disponibles"),
        ):
            response = self.client.post(reverse("lineup_helper"), {"action": "refresh"})

        self.assertContains(response, "1 cartas In-Season actualizadas")
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
