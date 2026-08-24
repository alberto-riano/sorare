#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/deploy/.env}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

sudo tee /etc/systemd/system/sorare-auctions-refresh.service >/dev/null <<EOF
[Unit]
Description=Sorare - sincronización de subastas Rare de LaLiga
After=network-online.target sorare-web.service
Wants=network-online.target

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR/web
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/web/manage.py refresh_auction_cache --full
Nice=10
EOF

sudo tee /etc/systemd/system/sorare-auctions-refresh.timer >/dev/null <<EOF
[Unit]
Description=Reconstruir completamente el mercado Sorare cada treinta minutos

[Timer]
OnBootSec=15s
OnUnitActiveSec=30min
Persistent=true
Unit=sorare-auctions-refresh.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sorare-auctions-refresh.timer
