#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

sudo tee /etc/systemd/system/sorare-nightly-sales-refresh.service >/dev/null <<EOF
[Unit]
Description=Sorare - encolar actualización nocturna de inventario y publicaciones
After=network-online.target sorare-sales-worker.service
Wants=network-online.target

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR/web
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/web/manage.py enqueue_nightly_sales_refresh
Nice=10
EOF

sudo tee /etc/systemd/system/sorare-nightly-sales-refresh.timer >/dev/null <<EOF
[Unit]
Description=Actualizar inventario y publicaciones cada madrugada

[Timer]
OnCalendar=*-*-* 04:00:00 Europe/Madrid
AccuracySec=5min
Persistent=true
Unit=sorare-nightly-sales-refresh.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sorare-nightly-sales-refresh.timer
