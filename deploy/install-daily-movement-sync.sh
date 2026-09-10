#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

sudo tee /etc/systemd/system/sorare-daily-movement-sync.service >/dev/null <<EOF
[Unit]
Description=Sorare - encolar actualización incremental diaria de movimientos
After=network-online.target sorare-sales-worker.service
Wants=network-online.target

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR/web
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/web/manage.py enqueue_daily_movement_sync
Nice=10
EOF

sudo tee /etc/systemd/system/sorare-daily-movement-sync.timer >/dev/null <<EOF
[Unit]
Description=Actualizar movimientos de burguis y blasco93 cada día a las 08:00 de Madrid

[Timer]
OnCalendar=*-*-* 08:00:00 Europe/Madrid
AccuracySec=1min
Persistent=true
Unit=sorare-daily-movement-sync.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sorare-daily-movement-sync.timer
