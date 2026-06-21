"""Capa de servicio web para renovar el JWT de Sorare (login + MFA).

Envuelve las funciones de ``renovar_token`` para que las vistas de Django
puedan iniciar el login en dos pasos (email/password -> codigo MFA) sin
depender de ``input()`` ni de ``print``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import renovar_token


def token_status() -> dict:
    """Estado del JWT actual para mostrar en la UI.

    Devuelve: {"valid", "expired", "expires_at_iso", "seconds_left"}.
    """
    status = renovar_token.get_token_status()
    expires_at_iso = None
    if status.get("expires_at"):
        expires_at_iso = datetime.fromtimestamp(
            status["expires_at"], tz=timezone.utc
        ).astimezone().strftime("%Y-%m-%d %H:%M")
    return {
        "valid": status["valid"],
        "expired": status["expired"],
        "expires_at_iso": expires_at_iso,
        "seconds_left": status["seconds_left"],
    }


def begin_refresh() -> dict:
    """Primer paso: usa EMAIL/PASSWORD de config.txt e inicia el login.

    Devuelve uno de:
      - {"status": "mfa_required", "otp_session_challenge": str}
      - {"status": "success"}  (token ya guardado en config.txt)
      - {"status": "error", "message": str}
    """
    try:
        result = renovar_token.start_login()
    except Exception as exc:  # noqa: BLE001 - mostrar el error en la UI
        return {"status": "error", "message": f"Fallo al contactar con Sorare: {exc}"}

    if result["status"] == "success":
        if result.get("token"):
            renovar_token.update_token_in_config(result["token"])
            return {"status": "success"}
        return {"status": "error", "message": "Sorare no devolvio token."}

    return result


def finish_refresh(otp_session_challenge: str, otp_code: str) -> dict:
    """Segundo paso: valida el codigo MFA y guarda el token nuevo.

    Devuelve {"status": "success"} o {"status": "error", "message": str}.
    """
    try:
        result = renovar_token.complete_login_with_otp(otp_session_challenge, otp_code)
    except Exception as exc:  # noqa: BLE001 - mostrar el error en la UI
        return {"status": "error", "message": f"Fallo al validar el MFA: {exc}"}

    if result["status"] == "success":
        if result.get("token"):
            renovar_token.update_token_in_config(result["token"])
            return {"status": "success"}
        return {"status": "error", "message": "Sorare no devolvio token tras el MFA."}

    return result
