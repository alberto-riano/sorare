from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-sorare-local-dev")
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS: list[str] = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()
]
CSRF_TRUSTED_ORIGINS: list[str] = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "dashboard.middleware.LoginRequiredMiddleware",
]

# WhiteNoise (servir estáticos en producción) solo si está instalado.
# Así el entorno local funciona aunque no tengas whitenoise; en la EC2 sí
# estará (requirements.txt) y se activa automáticamente.
try:
    import whitenoise  # noqa: F401

    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    _WHITENOISE_AVAILABLE = True
except ImportError:
    _WHITENOISE_AVAILABLE = False

ROOT_URLCONF = "sorare_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "sorare_web.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "es-es"
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Subpath / dominio compartido ---
# Si se sirve bajo una subruta (p.ej. /sorare en milesdepuntos.com), se indica
# con DJANGO_FORCE_SCRIPT_NAME=/sorare. En local se deja vacío y va en la raíz.
FORCE_SCRIPT_NAME = os.environ.get("DJANGO_FORCE_SCRIPT_NAME") or None
if FORCE_SCRIPT_NAME:
    _prefix = FORCE_SCRIPT_NAME.rstrip("/")
    STATIC_URL = f"{_prefix}/static/"
    SESSION_COOKIE_PATH = _prefix or "/"
    CSRF_COOKIE_PATH = _prefix or "/"

# Cookies con nombre propio para NO pisar la sesión de otros proyectos Django
# que compartan el mismo dominio (p.ej. crochet-shop / milesdepuntos).
SESSION_COOKIE_NAME = "sorare_sessionid"
CSRF_COOKIE_NAME = "sorare_csrftoken"

# --- Autenticación / sesión ---
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "index"
LOGOUT_REDIRECT_URL = "login"

# Mantener la sesión abierta mucho tiempo (no re-loguear cada vez).
SESSION_COOKIE_AGE = 60 * 60 * 24 * 365  # 1 año
SESSION_SAVE_EVERY_REQUEST = True         # renueva la expiración en cada visita
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# Endurecer cookies cuando se sirve por HTTPS (producción).
# Activa poniendo DJANGO_SECURE_COOKIES=true en el entorno de la EC2.
if os.environ.get("DJANGO_SECURE_COOKIES", "False").lower() == "true":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Estáticos servidos por WhiteNoise/nginx en producción (collectstatic).
STATIC_ROOT = BASE_DIR / "staticfiles"
if _WHITENOISE_AVAILABLE:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
