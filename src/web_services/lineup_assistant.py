"""Inventario y propuesta de alineaciones sin depender del Excel antiguo."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from pathlib import Path
import unicodedata

from openpyxl import load_workbook

from sorare_utils import build_headers, graphql_request


ROOT = Path(__file__).resolve().parents[2]
LEGACY_XLSX = ROOT / "input" / "lineups.xlsx"
POSITION_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
POSITION_LABELS = {"GK": "POR", "DEF": "DEF", "MID": "MED", "FWD": "DEL"}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or "").casefold())
    return " ".join("".join(char for char in value if unicodedata.category(char) != "Mn").split())


def position_code(values) -> str:
    if isinstance(values, (str, bytes)):
        values = [values]
    text = " ".join(str(value).casefold() for value in (values or []))
    if any(value in text for value in ("goalkeeper", "portero", "gk")):
        return "GK"
    if any(value in text for value in ("defender", "defensa", "def")):
        return "DEF"
    if any(value in text for value in ("midfielder", "midfield", "medio", "mid")):
        return "MID"
    if any(value in text for value in ("forward", "striker", "delantero", "fwd")):
        return "FWD"
    return ""


def legacy_averages(path: Path = LEGACY_XLSX) -> dict[str, float]:
    """Lee el último Excel sólo como migración inicial de medias manuales."""
    if not path.exists():
        return {}
    try:
        workbook = load_workbook(path, data_only=True, read_only=True)
        sheet = workbook[workbook.sheetnames[-1]]
        values: dict[str, float] = {}
        for row in sheet.iter_rows(values_only=True):
            cells = list(row)
            for index in range(len(cells) - 1):
                name, score = cells[index], cells[index + 1]
                if isinstance(name, str) and isinstance(score, (int, float)) and 0 <= score <= 100:
                    values[normalize(name)] = float(score)
        return values
    except Exception:
        return {}


def _legacy_average(name: str, values: dict[str, float]):
    normalized = normalize(name)
    if normalized in values:
        return values[normalized]
    matches = [(candidate, score) for candidate, score in values.items() if candidate in normalized or normalized in candidate]
    if len(matches) == 1:
        return matches[0][1]
    return None


def fetch_l15_averages(asset_ids: list[str], headers) -> dict[str, float | None]:
    """Lee L15 en lotes sin bloquear el refresco del inventario."""
    averages: dict[str, float | None] = {}
    for start in range(0, len(asset_ids), 20):
        batch = asset_ids[start:start + 20]
        fields = "\n".join(
            f'card_{index}: anyCard(assetId: "{asset_id}") {{ assetId averageScore(type: LAST_FIFTEEN_SO5_AVERAGE_SCORE) }}'
            for index, asset_id in enumerate(batch)
        )
        try:
            data = graphql_request(f"query LineupL15 {{ tokens {{ {fields} }} }}", headers=headers)
        except Exception:
            continue
        for item in (data.get("tokens") or {}).values():
            if isinstance(item, dict) and item.get("assetId"):
                averages[str(item["assetId"])] = item.get("averageScore")
    return averages


def fetch_lineup_cards(previous_cards: list[dict] | None = None) -> list[dict]:
    """Descarga tarjetas propias y conserva medias y candidatas locales."""
    previous = {str(card.get("asset_id")): card for card in (previous_cards or [])}
    old_averages = legacy_averages()
    headers = build_headers()
    cursor = None
    cards = []
    query = """
      query LineupInventory($after: String) {
        currentUser {
          cards(rarities: [rare], first: 100, after: $after) {
            nodes {
              assetId slug rarityTyped seasonYear serialNumber inSeasonEligible anyPositions
              anyPlayer { slug displayName squaredPictureUrl }
              anyTeam { name pictureUrl }
            }
            pageInfo { hasNextPage endCursor }
          }
          blockchainCardsInLineups
        }
      }
    """
    while True:
        data = graphql_request(query, {"after": cursor}, headers=headers)
        user = data.get("currentUser") or {}
        connection = user.get("cards") or {}
        lineup_slugs = set(user.get("blockchainCardsInLineups") or [])
        for raw in connection.get("nodes") or []:
            if str(raw.get("rarityTyped") or "").casefold() != "rare" or not raw.get("inSeasonEligible"):
                continue
            asset_id = str(raw.get("assetId") or "")
            if not asset_id:
                continue
            player = raw.get("anyPlayer") or {}
            team = raw.get("anyTeam") or {}
            old = previous.get(asset_id) or {}
            cards.append({
                "asset_id": asset_id,
                "slug": raw.get("slug") or "",
                "player": player.get("displayName") or "Jugador",
                "player_slug": player.get("slug") or "",
                "player_picture_url": player.get("squaredPictureUrl") or "",
                "team": team.get("name") or "Sin equipo",
                "team_picture_url": team.get("pictureUrl") or "",
                "rarity": raw.get("rarityTyped") or "",
                "season_year": raw.get("seasonYear"),
                "serial_number": raw.get("serialNumber"),
                "position": position_code(raw.get("anyPositions")),
                "is_in_season": bool(raw.get("inSeasonEligible")),
                "in_lineup": (raw.get("slug") or "") in lineup_slugs,
                "average": None,
                "sorare_average": None,
                # El pool ya no parte de toda la galería: sólo se usan las cartas
                # que el manager añade expresamente como candidatas.
                "candidate": bool(old.get("candidate", False)),
            })
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    l15_averages = fetch_l15_averages([card["asset_id"] for card in cards], headers)
    for card in cards:
        old = previous.get(str(card["asset_id"])) or {}
        average = l15_averages.get(str(card["asset_id"]))
        if average is None:
            average = old.get("average")
        if average is None:
            average = _legacy_average(card["player"], old_averages)
        card["sorare_average"] = l15_averages.get(str(card["asset_id"]))
        card["average"] = average
    return sorted(cards, key=lambda card: (POSITION_ORDER.get(card["position"], 9), card["player"].casefold(), card["serial_number"] or 0))


def load_odds(cards: list[dict]) -> tuple[dict, list[dict], str]:
    """Reutiliza el proveedor antiguo de cuotas; la herramienta funciona sin él."""
    try:
        import lineup_helper

        raw = lineup_helper.fetch_odds()
        teams = sorted({card.get("team") for card in cards if card.get("team")})
        odds, matches = lineup_helper.build_odds_map(raw, teams) if raw else ({}, [])
        return odds, matches, "Cuotas actualizadas" if odds else "Sin cuotas disponibles"
    except Exception:
        return {}, [], "Sin cuotas disponibles"


def attach_odds(cards: list[dict], odds: dict) -> list[dict]:
    attached = []
    for card in cards:
        row = dict(card)
        info = odds.get(card.get("team")) or {}
        row["win_probability"] = info.get("win_prob")
        row["win_percent"] = round(float(info["win_prob"]) * 100) if info.get("win_prob") is not None else None
        row["opponent"] = info.get("opponent") or ""
        row["home"] = info.get("home")
        attached.append(row)
    return attached


def _effective_score(card: dict, odds_weight: float) -> float:
    average = float(card.get("average") or 0)
    probability = card.get("win_probability")
    if probability is None:
        return average
    return round(average * (1 + (float(probability) - 0.5) * odds_weight), 2)


def _valid_lineups(cards: list[dict], max_points: int, odds_weight: float) -> list[tuple[float, list[dict]]]:
    by_position = {position: [] for position in POSITION_ORDER}
    for card in cards:
        if card.get("position") in by_position:
            by_position[card["position"]].append(card)
    for cards_at_position in by_position.values():
        cards_at_position.sort(key=lambda card: _effective_score(card, odds_weight), reverse=True)
        del cards_at_position[12:]
    result = []
    for goalkeeper in by_position["GK"]:
        for defenders, midfielders, forwards in ((2, 1, 1), (1, 2, 1), (1, 1, 2)):
            for defence in combinations(by_position["DEF"], defenders):
                for midfield in combinations(by_position["MID"], midfielders):
                    for attack in combinations(by_position["FWD"], forwards):
                        lineup = [goalkeeper, *defence, *midfield, *attack]
                        total = sum(float(card.get("average") or 0) for card in lineup)
                        if total > max_points:
                            continue
                        team_counts = Counter(card.get("team") for card in lineup)
                        if any(count > 2 for count in team_counts.values()):
                            continue
                        score = sum(_effective_score(card, odds_weight) for card in lineup)
                        if any(card.get("position") == "DEF" and card.get("team") == goalkeeper.get("team") for card in lineup):
                            score += 1
                        result.append((round(score, 2), lineup))
    return sorted(result, key=lambda item: item[0], reverse=True)


def propose_lineups(cards: list[dict], *, count: int = 4, max_points: int = 260, odds_weight: float = 0.3) -> dict:
    eligible = [
        card for card in cards
        if card.get("candidate") and card.get("position") and card.get("average") is not None
    ]
    selected, remaining = [], eligible
    for _ in range(count):
        candidates = _valid_lineups(remaining, max_points, odds_weight)
        if not candidates:
            break
        _, lineup = candidates[0]
        selected.append({
            "cards": lineup,
            "total": round(sum(float(card.get("average") or 0) for card in lineup), 1),
            "effective": round(sum(_effective_score(card, odds_weight) for card in lineup), 1),
        })
        ids = {card["asset_id"] for card in lineup}
        remaining = [card for card in remaining if card["asset_id"] not in ids]
    used = {card["asset_id"] for lineup in selected for card in lineup["cards"]}
    missing_average = [
        card for card in cards
        if card.get("candidate") and card.get("average") is None
    ]
    return {
        "lineups": selected,
        "used_count": len(used),
        "eligible_count": len(eligible),
        "bench": [card for card in eligible if card["asset_id"] not in used],
        "missing_average": missing_average,
        "max_points": max_points,
    }
