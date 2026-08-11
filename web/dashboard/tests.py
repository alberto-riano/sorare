from unittest.mock import patch

from django.test import SimpleTestCase

import listar_subastas


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
        response = {
            "tokens": {
                "liveAuctions": {
                    "totalCount": len(cards),
                    "nodes": [self._auction(card) for card in cards],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
        with patch.object(listar_subastas, "graphql_request", return_value=response):
            auctions, pages, total = listar_subastas.fetch_all_live_auctions(
                {}, rarity="rare", team_slugs={"real-madrid-madrid"}, season_year=2026
            )

        self.assertEqual([auction["asset_id"] for auction in auctions], ["wanted"])
        self.assertEqual((pages, total), (1, 4))

    @staticmethod
    def _card(asset_id, rarity, season, team_slug):
        return {
            "assetId": asset_id,
            "rarityTyped": rarity,
            "seasonYear": season,
            "serialNumber": 1,
            "anyPlayer": {"displayName": "Jugador", "slug": "jugador"},
            "anyTeam": {"name": "Equipo", "slug": team_slug},
            "anyPositions": ["Defender"],
        }

    @staticmethod
    def _auction(card):
        return {
            "id": f"EnglishAuction:{card['assetId']}",
            "currentPrice": "0",
            "endDate": "2026-08-12T12:00:00Z",
            "bestBid": None,
            "anyCards": [card],
        }
