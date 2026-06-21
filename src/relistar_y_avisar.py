#!/usr/bin/env python3
"""
Wrapper pensado para ejecutarse desde cron en la EC2.

Flujo:
  1. Comprueba si el JWT de Sorare sigue vigente.
  2. Si está caducado -> NO intenta relistar (fallaría con "Signature has
     expired"). En su lugar avisa por Telegram para que entres a la web y
     pulses "Renovar token" (el MFA impide renovarlo de forma desatendida).
  3. Si está vigente -> ejecuta la relista de cartas (ejecutar_ventas.py)
     para la(s) rareza(s) indicadas.

Uso:
  python relistar_y_avisar.py --rojas
  python relistar_y_avisar.py --rojas --amarillas --azules
  python relistar_y_avisar.py --rojas --margen-horas 12

Códigos de salida:
  0 = relista ejecutada (o nada que hacer)
  2 = token caducado/por caducar: requiere renovación manual (se avisó)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import renovar_token

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
EJECUTAR_VENTAS = os.path.join(BASE_DIR, "src", "ejecutar_ventas.py")


def _notify_telegram(text: str) -> None:
    """Envía un aviso por Telegram reutilizando la config existente."""
    try:
        config = renovar_token.read_config()
        bot_token = config.get("TELEGRAM_BOT_TOKEN")
        chat_id = config.get("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            print("Aviso: faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID; no se envía alerta.")
            return
        import requests

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=30)
    except Exception as exc:  # noqa: BLE001 - el cron no debe romper por esto
        print(f"No se pudo enviar el aviso de Telegram: {exc}")


def _rarity_flags(args) -> list[str]:
    flags = []
    if args.rojas:
        flags.append("--rojas")
    if args.amarillas:
        flags.append("--amarillas")
    if args.azules:
        flags.append("--azules")
    return flags or ["--rojas"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Relista cartas y avisa si el token caducó")
    parser.add_argument("--rojas", action="store_true", help="Relistar rare (rojas)")
    parser.add_argument("--amarillas", action="store_true", help="Relistar limited (amarillas)")
    parser.add_argument("--azules", action="store_true", help="Relistar super_rare (azules)")
    parser.add_argument(
        "--margen-horas",
        type=float,
        default=6.0,
        help="Avisar si el token caduca dentro de estas horas (por defecto 6)",
    )
    args = parser.parse_args()

    status = renovar_token.get_token_status()
    seconds_left = status.get("seconds_left")
    margin_seconds = args.margen_horas * 3600

    if status["expired"] or (seconds_left is not None and seconds_left <= margin_seconds):
        if status["expired"]:
            msg = "⚠️ Sorare: el token ha CADUCADO. Entra al panel y pulsa 'Renovar token' (te pedirá el MFA)."
        else:
            horas = round((seconds_left or 0) / 3600, 1)
            msg = f"⏳ Sorare: el token caduca en ~{horas}h. Renuévalo en el panel ('Renovar token') para no perder relistados."
        print(msg)
        _notify_telegram(msg)
        return 2

    # Token vigente -> relistar
    rc = 0
    for flag in _rarity_flags(args):
        cmd = [sys.executable, EJECUTAR_VENTAS, flag]
        print(f"Ejecutando: {' '.join(cmd)}")
        completed = subprocess.run(cmd, cwd=BASE_DIR, check=False)
        if completed.returncode != 0:
            rc = completed.returncode
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
