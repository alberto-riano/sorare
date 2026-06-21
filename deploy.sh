#!/usr/bin/env bash
#
# Deploy del panel de Sorare en la EC2 (igual filosofía que crochet-shop).
# Uso en la EC2:   cd /home/ubuntu/sorare && ./deploy.sh
#
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/sorare}"

cd "$PROJECT_DIR"

# Virtualenv (en este repo está en .venv)
source .venv/bin/activate

# Traer cambios del repo
git pull --ff-only

# Dependencias
pip install -r requirements.txt

# Cargar variables de entorno de producción (ALLOWED_HOSTS, subpath, etc.)
set -a
[ -f deploy/.env ] && source deploy/.env
set +a

# El proyecto Django vive en web/
cd web
python manage.py migrate
python manage.py collectstatic --no-input

# Reiniciar gunicorn de Sorare (servicio propio, no toca el de crochet-shop)
sudo systemctl restart sorare-web
sudo systemctl status sorare-web --no-pager --lines=5

echo "Deploy de Sorare completado"
