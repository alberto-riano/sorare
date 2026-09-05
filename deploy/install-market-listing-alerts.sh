#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

sudo tee /etc/systemd/system/sorare-market-listing-alert.service >/dev/null <<EOF
[Unit]
Description=Sorare - alertas Telegram de nuevas oportunidades a la venta
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/src/market_listing_alert.py
Nice=10
EOF

sudo tee /etc/systemd/system/sorare-market-listing-alert.timer >/dev/null <<EOF
[Unit]
Description=Comprobar nuevas ventas con oportunidad cada treinta minutos

[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
AccuracySec=30s
Persistent=true
Unit=sorare-market-listing-alert.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sorare-market-listing-alert.timer
