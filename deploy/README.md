# Despliegue en una EC2 Ubuntu

El repositorio incluye dos scripts:

- `setup.sh`: prepara una EC2 por primera vez (Python, Node, Gunicorn, systemd y nginx).
- `deploy.sh`: aplica las siguientes versiones con comprobaciones y health check.

## Primera instalación

En una EC2 Ubuntu con los puertos 22 y 80 abiertos (también 443 si tendrá HTTPS):

```bash
git clone URL_DEL_REPOSITORIO sorare
cd sorare
bash setup.sh
```

El instalador pregunta el dominio/IP y la subruta. Por defecto publica el panel
en `http://DOMINIO/sorare/`. Si la EC2 ya tiene un `server` de nginx para ese
dominio, no habilites dos bloques con el mismo `server_name`: copia los tres
`location` generados en `/etc/nginx/sites-available/sorare` al bloque existente
y deshabilita el nuevo con:

```bash
sudo unlink /etc/nginx/sites-enabled/sorare
sudo nginx -t && sudo systemctl reload nginx
```

La credencial de Sorare no viaja en Git. Desde el equipo local:

```bash
scp config/config.txt ubuntu@IP_EC2:/ruta/al/repo/config/config.txt
ssh ubuntu@IP_EC2 'chmod 600 /ruta/al/repo/config/config.txt'
```

Si se usa un dominio, activa TLS al terminar:

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d sorare.example.com
nano deploy/.env  # DJANGO_SECURE_COOKIES=true
./deploy.sh
```

No publiques este panel sin HTTPS: permite pujar y vender activos reales.

## Despliegues posteriores

Después de subir cambios al repositorio, en la EC2:

```bash
cd /ruta/al/repo
./deploy.sh
```

El script hace `git pull --ff-only`, instala dependencias Python y Node,
comprueba Django y migraciones pendientes, migra, recopila estáticos, reinicia
Gunicorn, mantiene activos los workers de pujas y ventas y comprueba `/healthz/`
directamente sobre el socket. Si encuentra
cambios locales inesperados, se detiene antes del pull.

## Datos que deben persistir

- `deploy/.env`: configuración Django, no versionada.
- `config/config.txt`: email, JWT y claves Sorare, no versionado.
- `web/db.sqlite3`: usuarios y sesiones del panel, no versionado.
- `output/`: Excel y logs generados, no versionado.

Haz copias de esos datos, especialmente `config/config.txt` y `web/db.sqlite3`.
Para automatizaciones, adapta `deploy/crontab.example` a la ruta real del repo.

Comandos útiles:

```bash
sudo systemctl status sorare-web
sudo systemctl status sorare-sales-worker
sudo journalctl -u sorare-web -n 100 --no-pager
sudo journalctl -u sorare-sales-worker -n 100 --no-pager
sudo nginx -t
```
