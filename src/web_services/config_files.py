from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_TELEGRAM_SETTINGS = {
    "AUCTION_ALERT_ENABLED": "false",
    "AUCTION_ALERT_MINUTES": "3",
    "AUCTION_ALERT_MIN_SAVING_PERCENT": "20",
    "AUCTION_ALERT_RARITIES": "rare,super_rare",
    "MARKET_ALERT_ENABLED": "false",
    "MARKET_ALERT_MIN_SAVING_PERCENT": "25",
    "MARKET_ALERT_MIN_LIMITED_VALUE_EUR": "1",
    "MARKET_ALERT_MIN_COMPARABLES": "0",
    "NOTIFY_MODE": "all",
    "NOTIFY_DROP_EUR": "1.0",
    "SEND_ALL_OFFERS_BELOW_THRESHOLD": "true",
    "SEND_RUN_START_MESSAGE": "true",
    "SEND_SINGLE_MESSAGE": "false",
    "INCLUDE_PLAYER_PREVIEW": "true",
    "RARITY": "rare",
    "SEASON_YEAR": "",
    "IN_SEASON_YEAR": "2025",
}

SETTINGS_ORDER = [
    "AUCTION_ALERT_ENABLED",
    "AUCTION_ALERT_MINUTES",
    "AUCTION_ALERT_MIN_SAVING_PERCENT",
    "AUCTION_ALERT_RARITIES",
    "MARKET_ALERT_ENABLED",
    "MARKET_ALERT_MIN_SAVING_PERCENT",
    "MARKET_ALERT_MIN_LIMITED_VALUE_EUR",
    "MARKET_ALERT_MIN_COMPARABLES",
    "NOTIFY_MODE",
    "NOTIFY_DROP_EUR",
    "SEND_ALL_OFFERS_BELOW_THRESHOLD",
    "SEND_RUN_START_MESSAGE",
    "SEND_SINGLE_MESSAGE",
    "INCLUDE_PLAYER_PREVIEW",
    "RARITY",
    "SEASON_YEAR",
    "IN_SEASON_YEAR",
]

PLAYERS_HEADER = (
    "# desired_players.txt\n"
    "# Formato: <player name> <threshold_eur> [min_level]\n"
    "# Example: eder militao 13 8\n"
)


@dataclass(frozen=True)
class SorarePaths:
    repo_root: Path

    @property
    def config_dir(self) -> Path:
        return self.repo_root / "config"

    @property
    def src_dir(self) -> Path:
        return self.repo_root / "src"

    @property
    def telegram_settings_file(self) -> Path:
        return self.config_dir / "telegram_alert_settings.txt"

    @property
    def desired_players_file(self) -> Path:
        return self.config_dir / "desired_players.txt"

    @property
    def desired_players_in_season_file(self) -> Path:
        return self.config_dir / "desired_players_in_season.txt"


def parse_key_value_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_key_value_file(path: Path, values: dict[str, str], ordered_keys: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Generated from Sorare Web UI", ""]
    for key in ordered_keys:
        lines.append(f"{key}={values.get(key, '').strip()}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def load_players_file(path: Path) -> str:
    if not path.exists():
        return ""
    rows: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(line)
    return "\n".join(rows)


def write_players_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_rows = [line.strip() for line in text.splitlines() if line.strip()]
    payload = PLAYERS_HEADER + "\n" + "\n".join(clean_rows) + "\n"
    path.write_text(payload, encoding="utf-8")


def load_telegram_alert_payload(paths: SorarePaths) -> dict[str, str]:
    current = parse_key_value_file(paths.telegram_settings_file)
    merged = {**DEFAULT_TELEGRAM_SETTINGS, **current}
    merged["classic_players"] = load_players_file(paths.desired_players_file)
    merged["in_season_players"] = load_players_file(paths.desired_players_in_season_file)
    return merged


def save_telegram_alert_payload(paths: SorarePaths, payload: dict[str, str]) -> None:
    file_values = {key: str(payload.get(key, "")).strip() for key in SETTINGS_ORDER}
    write_key_value_file(paths.telegram_settings_file, file_values, SETTINGS_ORDER)
    write_players_file(paths.desired_players_file, payload.get("classic_players", ""))
    write_players_file(paths.desired_players_in_season_file, payload.get("in_season_players", ""))
