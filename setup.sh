#!/usr/bin/env bash
# Provision inicial, idempotente, para una EC2 Ubuntu.
# Ejecutar como usuario normal desde la raiz del repositorio: bash setup.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/deploy/.env"
SERVICE_NAME="sorare-web"
SERVICE_USER="$(id -un)"

info() { printf '==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$SERVICE_USER" != "root" ] || fail "Ejecuta setup.sh como ubuntu (usara sudo cuando sea necesario)."
command -v apt-get >/dev/null 2>&1 || fail "Este instalador esta preparado para Ubuntu/Debian."

printf 'Dominio o IP publica (ej. sorare.example.com): '
read -r PUBLIC_HOST
[ -n "$PUBLIC_HOST" ] || fail "El dominio o IP es obligatorio."
printf 'Subruta web [/sorare] (usa / para servirlo en la raiz): '
read -r URL_PREFIX
URL_PREFIX="${URL_PREFIX:-/sorare}"
[ "$URL_PREFIX" = "/" ] && URL_PREFIX=""
URL_PREFIX="${URL_PREFIX%/}"
[[ "$URL_PREFIX" != *" "* ]] || fail "La subruta no puede contener espacios."

SCHEME="http"

info "Instalando paquetes del sistema"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-dev build-essential nginx curl git nodejs npm

# @sorare/crypto y las utilidades actuales requieren un Node moderno. Algunas
# versiones LTS de Ubuntu aun distribuyen Node 18 en apt.
NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
if [ "$NODE_MAJOR" -lt 20 ]; then
    info "Actualizando Node.js a la rama 20 LTS"
    sudo npm install --global n
    sudo n 20
    hash -r
fi
[ "$(node -p 'Number(process.versions.node.split(".")[0])')" -ge 20 ] || fail "Se necesita Node.js 20 o superior."

info "Creando entorno e instalando dependencias"
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
npm --prefix "$PROJECT_DIR" ci --no-audit --no-fund
npm --prefix "$PROJECT_DIR/javascript" ci --no-audit --no-fund

if [ ! -f "$ENV_FILE" ]; then
    SECRET_KEY="$("$VENV_DIR/bin/python" -c 'import secrets; print(secrets.token_urlsafe(64))')"
    umask 077
    {
        printf "DJANGO_SECRET_KEY='%s'\n" "$SECRET_KEY"
        printf "DJANGO_DEBUG=False\n"
        printf "DJANGO_ALLOWED_HOSTS='%s,localhost,127.0.0.1'\n" "$PUBLIC_HOST"
        printf "DJANGO_FORCE_SCRIPT_NAME='%s'\n" "$URL_PREFIX"
        printf "DJANGO_CSRF_TRUSTED_ORIGINS='http://%s,https://%s'\n" "$PUBLIC_HOST" "$PUBLIC_HOST"
        printf "DJANGO_SECURE_COOKIES=false\n"
        printf "SORARE_SOCKET_PATH='/run/sorare/gunicorn.sock'\n"
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
else
    info "Se conserva el entorno existente: $ENV_FILE"
fi

mkdir -p "$PROJECT_DIR/output"
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
cd "$PROJECT_DIR/web"
"$VENV_DIR/bin/python" manage.py migrate --no-input
"$VENV_DIR/bin/python" manage.py collectstatic --no-input

info "Instalando servicio systemd"
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=Sorare Command Center (Django + Gunicorn)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=www-data
UMask=007
RuntimeDirectory=sorare
RuntimeDirectoryMode=0750
WorkingDirectory=$PROJECT_DIR/web
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/gunicorn --workers 2 --timeout 600 --bind unix:/run/sorare/gunicorn.sock sorare_web.wsgi:application
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" SERVICE_USER="$SERVICE_USER" \
    bash "$PROJECT_DIR/deploy/install-auction-refresh.sh"
PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" SERVICE_USER="$SERVICE_USER" \
    bash "$PROJECT_DIR/deploy/install-bid-worker.sh"
PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" SERVICE_USER="$SERVICE_USER" \
    bash "$PROJECT_DIR/deploy/install-sales-worker.sh"
PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" SERVICE_USER="$SERVICE_USER" \
    bash "$PROJECT_DIR/deploy/install-opportunity-refresh.sh"
PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" SERVICE_USER="$SERVICE_USER" \
    bash "$PROJECT_DIR/deploy/install-auction-value-alerts.sh"
PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" SERVICE_USER="$SERVICE_USER" \
    bash "$PROJECT_DIR/deploy/install-market-listing-alerts.sh"

NGINX_SITE="/etc/nginx/sites-available/sorare"
REDIRECT_LOCATION=""
if [ -n "$URL_PREFIX" ]; then
    REDIRECT_LOCATION="location = $URL_PREFIX { return 301 $URL_PREFIX/; }"
fi
info "Instalando configuracion nginx en $NGINX_SITE"
sudo tee "$NGINX_SITE" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $PUBLIC_HOST;

    $REDIRECT_LOCATION

    location ${URL_PREFIX}/ {
        rewrite ^${URL_PREFIX:-}/(.*) /\$1 break;
        proxy_pass http://unix:/run/sorare/gunicorn.sock;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 600s;
        client_max_body_size 25M;
    }
}
EOF
sudo ln -sfn "$NGINX_SITE" /etc/nginx/sites-enabled/sorare
sudo nginx -t
sudo systemctl reload nginx

if ! "$VENV_DIR/bin/python" manage.py shell -c 'from django.contrib.auth import get_user_model; raise SystemExit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)'; then
    printf '\nCrea ahora el usuario con acceso al panel.\n'
    "$VENV_DIR/bin/python" manage.py createsuperuser
fi

printf '\nInstalacion terminada: http://%s%s/\n' "$PUBLIC_HOST" "$URL_PREFIX"
if [[ ! "$PUBLIC_HOST" =~ ^[0-9.]+$ ]]; then
    printf 'Activa HTTPS con: sudo apt-get install certbot python3-certbot-nginx && sudo certbot --nginx -d %s\n' "$PUBLIC_HOST"
    printf 'Despues cambia DJANGO_SECURE_COOKIES=true en %s y ejecuta ./deploy.sh\n' "$ENV_FILE"
fi
