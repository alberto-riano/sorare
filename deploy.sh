#!/usr/bin/env bash
# Actualiza una instalacion existente de Sorare Command Center.
# La primera instalacion se realiza con: bash setup.sh
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env}"
SERVICE_NAME="${SERVICE_NAME:-sorare-web}"
DEPLOY_STATE_DIR="$PROJECT_DIR/.deploy"

info() { printf '==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cd "$PROJECT_DIR"
[ -x "$VENV_DIR/bin/python" ] || fail "No existe $VENV_DIR. Ejecuta primero: bash setup.sh"
[ -f "$ENV_FILE" ] || fail "Falta $ENV_FILE. Ejecuta primero: bash setup.sh"

# Un pull con cambios locales puede mezclar o bloquear un despliegue. Los ficheros
# de configuracion que edita la propia web se permiten si no se han modificado en
# remoto; git pull --ff-only seguira protegiendo frente a un conflicto real.
DIRTY_FILES="$(git status --porcelain | awk '{print $2}')"
UNEXPECTED_DIRTY="$(printf '%s\n' "$DIRTY_FILES" | grep -Ev '^(config/(desired_players(_in_season)?|telegram_alert_settings)\.txt|)$' || true)"
[ -z "$UNEXPECTED_DIRTY" ] || fail "Hay cambios locales en el servidor fuera de config/:\n$UNEXPECTED_DIRTY"

mkdir -p "$DEPLOY_STATE_DIR"
PREVIOUS_SHA="$(git rev-parse HEAD)"
printf '%s\n' "$PREVIOUS_SHA" > "$DEPLOY_STATE_DIR/previous_sha"

info "Descargando cambios"
git pull --ff-only
DEPLOY_SHA="$(git rev-parse HEAD)"
printf '%s\n' "$DEPLOY_SHA" > "$DEPLOY_STATE_DIR/current_sha"

info "Instalando dependencias Python"
"$VENV_DIR/bin/pip" install --disable-pip-version-check -r requirements.txt

if command -v npm >/dev/null 2>&1; then
    info "Instalando dependencias Node para las operaciones firmadas"
    npm ci --no-audit --no-fund
    npm --prefix javascript ci --no-audit --no-fund
else
    fail "Node/npm no esta instalado y es necesario para pujas y ventas. Ejecuta setup.sh."
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

info "Validando Django y aplicando cambios"
cd "$PROJECT_DIR/web"
"$VENV_DIR/bin/python" manage.py check --deploy
"$VENV_DIR/bin/python" manage.py makemigrations --check --dry-run
"$VENV_DIR/bin/python" manage.py migrate --no-input
"$VENV_DIR/bin/python" manage.py collectstatic --no-input

PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" \
    bash "$PROJECT_DIR/deploy/install-auction-refresh.sh"
PROJECT_DIR="$PROJECT_DIR" VENV_DIR="$VENV_DIR" ENV_FILE="$ENV_FILE" \
    bash "$PROJECT_DIR/deploy/install-bid-worker.sh"

info "Reiniciando $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

SOCKET_PATH="${SORARE_SOCKET_PATH:-/run/sorare/gunicorn.sock}"
HEALTH_PATH="/healthz/"
HTTP_STATUS="000"
for _attempt in {1..30}; do
    HTTP_STATUS="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --unix-socket "$SOCKET_PATH" -H 'Host: localhost' "http://localhost${HEALTH_PATH}" 2>/dev/null || true)"
    [ "$HTTP_STATUS" = "200" ] && break
    sleep 1
done

if [ "$HTTP_STATUS" != "200" ]; then
    printf 'ERROR: health check fallido (HTTP %s). Commit anterior: %s\n' "$HTTP_STATUS" "$PREVIOUS_SHA" >&2
    sudo systemctl status "$SERVICE_NAME" --no-pager --lines=30 || true
    exit 1
fi

sudo systemctl status "$SERVICE_NAME" --no-pager --lines=5
info "Despliegue correcto: ${DEPLOY_SHA:0:12}"
