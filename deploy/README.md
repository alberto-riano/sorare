# Desplegar Sorare en tu EC2 (milesdepuntos.com/sorare)

Guía adaptada a TU setup: una EC2 (`51.21.117.59`) que ya sirve
`crochet-shop` en `www.milesdepuntos.com` con gunicorn + nginx. Aquí añadimos
el panel de Sorare en `www.milesdepuntos.com/sorare`, con su **propio
gunicorn** (socket aparte) y un bloque `location` en tu nginx. Crochet-shop no
se toca.

Tu forma de trabajar se mantiene: me pides cambios → `git push` desde tu
local → en la EC2 `./deploy.sh` → ves los cambios en la URL.

> ⚠️ **Seguridad.** El panel compra/vende/puja con dinero real y guarda
> secretos (incluida la clave privada de Solana en `config/config.txt`).
> Por eso: login obligatorio (usuario `burguis`), HTTPS (ya lo tienes en
> milesdepuntos) y `config/config.txt` **nunca** en Git (está en `.gitignore`;
> se sube por `scp`).

---

## A. Primera instalación en la EC2 (una sola vez)

### 1. Clonar el repo en /home/ubuntu/sorare

```bash
ssh -i /Users/albertorianogonzalez/Desktop/workspace/crochet-shop/milesdepuntos.pem ubuntu@51.21.117.59

cd /home/ubuntu
git clone TU_REPO_GIT sorare
cd sorare
```

### 2. Virtualenv + dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Subir los secretos (desde TU portátil, NO en la EC2)

`config/config.txt` no está en Git. En otra terminal de tu Mac:

```bash
scp -i /Users/albertorianogonzalez/Desktop/workspace/crochet-shop/milesdepuntos.pem \
    config/config.txt \
    ubuntu@51.21.117.59:/home/ubuntu/sorare/config/config.txt
```

### 4. Variables de entorno de producción

```bash
cd /home/ubuntu/sorare
cp deploy/.env.example deploy/.env
nano deploy/.env      # genera DJANGO_SECRET_KEY; el resto ya viene listo para milesdepuntos
```

Genera la secret key con:
`python -c "import secrets; print(secrets.token_urlsafe(50))"`

### 5. Migraciones, estáticos y tu usuario `burguis`

```bash
cd /home/ubuntu/sorare/web
set -a && source ../deploy/.env && set +a

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser     # usuario: burguis  (y la contraseña que quieras)
```

### 6. Servicio gunicorn de Sorare (systemd)

```bash
sudo cp /home/ubuntu/sorare/deploy/sorare-web.service /etc/systemd/system/sorare-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now sorare-web
sudo systemctl status sorare-web        # active (running) y crea gunicorn.sock
```

### 7. Enrutar /sorare en tu nginx existente

Edita tu archivo de nginx de milesdepuntos (el `shop.conf`) y pega DENTRO del
`server { ... }` del 443 los bloques `location` de
`deploy/nginx-sorare.conf`:

```bash
sudo nano /etc/nginx/sites-available/shop.conf      # o donde tengas tu server block
# pega los location de /home/ubuntu/sorare/deploy/nginx-sorare.conf
sudo nginx -t && sudo systemctl reload nginx
```

Abre `https://www.milesdepuntos.com/sorare/` → verás el login. Entra con
`burguis`.

---

## B. Día a día (tu flujo habitual)

En tu Mac, cuando tengamos cambios:

```bash
git add -A && git commit -m "cambios" && git push
```

En la EC2:

```bash
cd /home/ubuntu/sorare && ./deploy.sh
```

`deploy.sh` hace: `git pull` → `pip install` → `migrate` → `collectstatic` →
`systemctl restart sorare-web`. Recarga la URL y ves los cambios.

> La primera vez, da permisos de ejecución: `chmod +x /home/ubuntu/sorare/deploy.sh`

---

## C. Crontab (relistar cartas + aviso de token)

El JWT caduca y, por el MFA, **no se renueva solo**. El cron relista mientras
el token esté vigente y te **avisa por Telegram** cuando vaya a caducar, para
que entres a `…/sorare/token/` y pulses **Renovar token** (te pedirá el MFA).

```bash
crontab -e
# pega y ajusta deploy/crontab.example
crontab -l
```

---

## D. Renovar el token desde la web (con MFA)

Cuando veas `Unauthorized: Signature has expired` o el aviso de Telegram:

1. `https://www.milesdepuntos.com/sorare/token/` (o el botón "Renovar token"
   que aparece en *Ofertas Recibidas*).
2. **Iniciar sesión** (usa el `EMAIL`/`PASSWORD` de `config.txt`).
3. Introduce el **código MFA de 6 dígitos** y **Validar y renovar**.
4. El nuevo JWT se guarda solo en `config/config.txt`.

---

## Notas

- **Por qué un gunicorn aparte:** cada proyecto Django tiene su socket y su
  servicio systemd. Así Sorare y crochet-shop son independientes; reiniciar
  uno no afecta al otro.
- **Subpath:** `DJANGO_FORCE_SCRIPT_NAME=/sorare` hace que todos los enlaces
  internos lleven el prefijo `/sorare`. nginx quita el prefijo antes de pasar
  la petición a gunicorn.
- **Cookies:** Sorare usa `sorare_sessionid`/`sorare_csrftoken` para no pisar
  la sesión de crochet-shop en el mismo dominio.
- **Sesión larga:** configurada a 1 año y se renueva en cada visita, así no
  tienes que re-loguearte constantemente.
