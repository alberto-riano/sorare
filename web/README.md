# Sorare Web UI (Django)

## Start

From repository root:

```bash
. .venv/bin/activate
cd web
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000

## Implemented modules

- Telegram Alerts
  - Edit alert settings from web form.
  - Edit classic and in-season player lists.
  - Save config files used by CLI scripts.
  - Run dry-run and real execution from UI.

- Bid Scheduler
  - Schedule bid with identifier, amount, time, now/sniper/background and credit mode.
  - Execute existing script from UI and display stdout/stderr.

- Sales Workbench (new)
  - Export cards by rarity from UI (blue limited or red rare).
  - Download generated Excel file.
  - Edit "Precio venta" directly in web table and persist to Excel.
  - Review cards with price and confirm which ones to sell.
  - Execute selected sales with day duration and get result summary.

## CLI compatibility

All existing scripts remain available from terminal.
