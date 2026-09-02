#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

sudo tee /etc/systemd/system/sorare-auction-value-alert.service >/dev/null <<EOF
[Unit]
Description=Sorare - alertas Telegram de oportunidades en subastas
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/src/auction_value_alert.py
Nice=10
EOF

sudo tee /etc/systemd/system/sorare-auction-value-alert.timer >/dev/null <<EOF
[Unit]
Description=Comprobar oportunidades de subasta cada minuto

[Timer]
OnBootSec=45s
OnUnitActiveSec=1min
AccuracySec=5s
Persistent=true
Unit=sorare-auction-value-alert.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sorare-auction-value-alert.timer
