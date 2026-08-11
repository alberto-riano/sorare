"""Middleware que exige autenticación para todo el dashboard.

Sin esto, cualquiera con acceso a la IP/puerto podría operar la cuenta de
Sorare (comprar, vender, pujar) o ver secretos. Por eso protegemos todas las
rutas excepto el login y los estáticos.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            # request.path incluye el prefijo de subruta (FORCE_SCRIPT_NAME),
            # igual que reverse(), por eso lo usamos en lugar de path_info.
            path = request.path
            login_url = reverse("login")
            logout_url = reverse("logout")
            health_url = reverse("healthz")
            allowed = (login_url, logout_url, health_url, settings.STATIC_URL)
            if not any(path.startswith(prefix) for prefix in allowed):
                return redirect(f"{login_url}?next={path}")
        return self.get_response(request)
