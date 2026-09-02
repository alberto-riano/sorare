from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

from .config_files import SorarePaths


@dataclass(frozen=True)
class ScriptResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class BidRequest:
    identifier: str
    euros: str
    hora: str
    now: bool
    sniper: bool
    background: bool
    use_credit: bool
    currency: str = "EUR"


def bid_error_message(result: ScriptResult, *, max_length: int = 500) -> str:
    """Extrae un error legible de la salida de una puja y oculta posibles secretos."""
    raw = result.stderr or result.stdout or "Sorare no devolvió una descripción del error."
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    lines = [line.strip().lstrip("❌").strip() for line in raw.splitlines() if line.strip()]
    ignored = (
        "error al pujar (exit code:", "comando: node", "pujar en subasta de sorare",
        "auction id:", "obteniendo info", "cantidad:", "====",
    )
    useful = [line for line in lines if not line.casefold().startswith(ignored)]
    detail = " · ".join(useful[-3:] if useful else lines[-1:])
    detail = re.sub(
        r"(?i)(authorization|bearer|jwt_token|private_key)(?:\s*[:=]\s*|\s+)\S+",
        r"\1: [oculto]",
        detail,
    )
    if not detail:
        detail = "Sorare no devolvió una descripción del error."
    return detail[:max_length]


def sale_error_message(result: ScriptResult, *, max_length: int = 500) -> str:
    """Extrae el mensaje útil de Sorare sin mostrar credenciales ni el comando."""
    raw = result.stderr or result.stdout or "Sorare no devolvió una descripción del error."
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    lines = [line.strip().lstrip("❌").strip() for line in raw.splitlines() if line.strip()]
    ignored = ("tipo recibido:", "errores preparando la oferta:", "errores creando la oferta:")
    useful = [line for line in lines if not line.casefold().startswith(ignored)]
    detail = " · ".join(useful[-3:] if useful else lines[-1:])
    detail = re.sub(
        r"(?i)(authorization|bearer|jwt_token|private_key)(?:\s*[:=]\s*|\s+)\S+",
        r"\1: [oculto]",
        detail,
    )
    return (detail or "Sorare no devolvió una descripción del error.")[:max_length]


def _run_command(cmd: list[str], cwd: Path) -> ScriptResult:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return ScriptResult(
        command=" ".join(cmd),
        exit_code=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def run_telegram_alert(paths: SorarePaths, *, dry_run: bool = False) -> ScriptResult:
    cmd = [
        sys.executable,
        str(paths.src_dir / "alerta_telegram.py"),
        "--settings-file",
        str(paths.telegram_settings_file),
        "--desired-file",
        str(paths.desired_players_file),
    ]
    if dry_run:
        cmd.append("--dry-run")
    return _run_command(cmd, paths.repo_root)


def run_auction_value_alert(paths: SorarePaths, *, dry_run: bool = False) -> ScriptResult:
    cmd = [sys.executable, str(paths.src_dir / "auction_value_alert.py")]
    if dry_run:
        cmd.append("--dry-run")
    return _run_command(cmd, paths.repo_root)


def run_bid_scheduler(paths: SorarePaths, request: BidRequest) -> ScriptResult:
    cmd = [
        sys.executable,
        str(paths.src_dir / "programar_puja.py"),
        request.identifier.strip(),
        request.euros.strip(),
    ]

    if request.hora.strip():
        cmd.append(request.hora.strip())

    if request.now:
        cmd.append("--now")
    if request.sniper:
        cmd.append("--sniper")
    if request.background:
        cmd.append("--bg")
    if request.use_credit:
        cmd.append("--use-credit")
    else:
        cmd.append("--no-credit")
    cmd.extend(("--currency", request.currency.upper()))

    return _run_command(cmd, paths.repo_root)


def run_card_sale(
    paths: SorarePaths,
    *,
    asset_id: str,
    euros: str,
    duration_days: int,
    minimum_offer_eur: str | None = None,
) -> ScriptResult:
    price_cents = int(round(float(euros) * 100))
    minimum_offer_cents = int(round(float(minimum_offer_eur) * 100)) if minimum_offer_eur else 0
    cmd = [
        "node",
        str(paths.repo_root / "javascript" / "vender_carta.js"),
        asset_id.strip(),
        str(price_cents),
        str(int(duration_days)),
        str(minimum_offer_cents),
        "--no-relist",
    ]
    return _run_command(cmd, paths.repo_root)


def run_direct_offer(
    paths: SorarePaths,
    *,
    asset_id: str,
    manager_slug: str,
    euros: str,
    duration_hours: int = 48,
    currency: str = "EUR",
) -> ScriptResult:
    currency = str(currency or "EUR").strip().upper()
    if currency not in {"EUR", "ETH"}:
        raise ValueError("La moneda de pago debe ser EUR o ETH")
    amount_cents = int(round(float(euros) * 100))
    cmd = [
        "node",
        str(paths.repo_root / "javascript" / "vender_carta.js"),
        "--direct-offer",
        asset_id.strip(),
        manager_slug.strip(),
        str(amount_cents),
        str(int(duration_hours)),
        "--currency",
        currency,
    ]
    return _run_command(cmd, paths.repo_root)
