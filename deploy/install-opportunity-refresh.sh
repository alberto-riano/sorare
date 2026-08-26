#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

sudo tee /etc/systemd/system/sorare-opportunities-refresh.service >/dev/null <<EOF
[Unit]
Description=Sorare - encolar análisis de oportunidades LaLiga
After=network-online.target sorare-sales-worker.service
Wants=network-online.target

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR/web
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/web/manage.py enqueue_opportunity_refresh
Nice=10
EOF

sudo tee /etc/systemd/system/sorare-opportunities-refresh.timer >/dev/null <<EOF
[Unit]
Description=Actualizar oportunidades Sorare cada dos horas

[Timer]
OnBootSec=10min
OnUnitActiveSec=2h
Persistent=true
Unit=sorare-opportunities-refresh.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sorare-opportunities-refresh.timer
