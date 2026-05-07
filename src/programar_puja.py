#!/usr/bin/env python3
# ============================================================
# CONFIGURACIÓN 
# ============================================================
DEFAULT_IDENTIFIER = "https://sorare.com/football/players/karl-edouard-blaise-etta-eyong/cards?s=Lowest%20Price&rarity=rare&sale=true&is=true&card=karl-edouard-blaise-etta-eyong-2025-rare-88"
DEFAULT_EUROS = 6       # Cantidad en euros
DEFAULT_HORA = "13:27:12"   # Hora España HH:MM o HH:MM:SS
NOW = True                   # True = pujar YA, ignora la hora
BG = False                    # True = ejecutar en segundo plano (puedes cerrar VS Code)
USE_CREDIT = True             # True = usar créditos de conversión disponibles al pujar
# ============================================================
"""
Programa una puja en Sorare a una hora específica (hora España).
Si NOW = True, puja inmediatamente ignorando la hora.

Uso:
    python3 programar_puja.py                               # usa defaults de arriba
    python3 programar_puja.py <auction_id> <euros> <hora>
    python3 programar_puja.py --now                         # fuerza puja inmediata
    python3 programar_puja.py --bg                          # en segundo plano
"""
import sys
import os
import subprocess
import time
import signal
import argparse
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sorare_utils import graphql_request, build_headers

SPAIN_TZ = ZoneInfo("Europe/Madrid")
JS_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'javascript', 'pujar_carta.js')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output')
PID_FILE = os.path.join(OUTPUT_DIR, 'puja_programada.pid')


def now_spain():
    return datetime.now(SPAIN_TZ)


def parse_time_spain(time_str):
    """Parsea HH:MM o HH:MM:SS y devuelve un datetime hoy en hora España."""
    today = now_spain().date()
    parts = time_str.split(':')
    if len(parts) == 2:
        h, m = int(parts[0]), int(parts[1])
        s = 0
    elif len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError(f"Formato de hora inválido: {time_str}. Usa HH:MM o HH:MM:SS")

    target = datetime(today.year, today.month, today.day, h, m, s, tzinfo=SPAIN_TZ)

    # Si la hora ya pasó, asumir que es mañana
    if target <= now_spain():
        target += timedelta(days=1)
        print(f"  ⚠️  Esa hora ya pasó hoy, programando para mañana ({target.strftime('%Y-%m-%d')})")

    return target


def resolve_auction_id(identifier, headers):
    """
    Resuelve el identifier a un auction_id.
    Acepta:
    - 'EnglishAuction:...' → lo devuelve tal cual
    - '0x...' → busca subasta activa por asset_id
    - URL de Sorare (copia desde el navegador) → extrae la carta y busca subasta
    """
    import re
    from urllib.parse import urlparse, parse_qs

    # Caso 1: ya es un auction_id
    if identifier.startswith('EnglishAuction:'):
        return identifier

    # Caso 2: es un asset_id
    if identifier.startswith('0x'):
        return _find_auction_by_asset_id(identifier, headers)

    # Caso 3: URL de Sorare
    if 'sorare.com' in identifier:
        parsed = urlparse(identifier)
        params = parse_qs(parsed.query)

        # URL tipo market con ?card=slug-name-year-rarity-serial
        if 'card' in params:
            card_slug = params['card'][0]  # ej: francisco-roman-alarcon-suarez-2025-rare-79
            print(f"🔍 Carta extraída de URL: {card_slug}")
            return _find_auction_by_card_slug(card_slug, headers)

        # URL tipo /football/players/slug
        player_match = re.search(r'/football/players/([a-z0-9-]+)', parsed.path)
        if player_match:
            slug = player_match.group(1)
            print(f"🔍 Slug de jugador extraído de URL: {slug}")
            return _find_auction_by_player_slug(slug, headers)

        print(f"❌ No se pudo extraer info de la URL: {identifier}")
        print("   Formatos válidos:")
        print("   - URL de subasta: https://sorare.com/football/market/shop/auctions?...&card=nombre-year-rarity-serial")
        print("   - URL de jugador: https://sorare.com/football/players/slug-del-jugador")
        sys.exit(1)

    # No reconocido
    print(f"❌ Formato no reconocido: {identifier}")
    print("   Formatos válidos:")
    print("   - Asset ID:  0x04003... (lo ves en el script ListLaLigaAuctions)")
    print("   - URL:       copia la URL de la carta/jugador desde sorare.com")
    sys.exit(1)


def _find_auction_by_asset_id(asset_id, headers):
    """Busca subasta activa por asset_id (0x...)."""
    print(f"🔍 Buscando subasta activa para asset_id: {asset_id[:20]}...")
    query = '''
    query GetCardAuction($assetIds: [String!]!) {
      football {
        allCards(assetIds: $assetIds, first: 1) {
          nodes {
            anyPlayer { displayName }
            anyTeam { name }
            latestEnglishAuction {
              id
              open
              endDate
              bestBid {
                amounts { eurCents }
                bidder { ... on User { nickname } }
              }
            }
          }
        }
      }
    }
    '''
    data = graphql_request(query, {'assetIds': [asset_id]}, headers=headers)
    nodes = data['football']['allCards']['nodes']
    if not nodes:
        print("❌ No se encontró ninguna carta con ese asset_id")
        sys.exit(1)

    card = nodes[0]
    auction = card.get('latestEnglishAuction')
    if not auction or not auction.get('open'):
        print(f"❌ La carta de {card['anyPlayer']['displayName']} no tiene subasta activa")
        sys.exit(1)

    print(f"   ✅ {card['anyPlayer']['displayName']} ({card['anyTeam']['name']})")
    print(f"   Auction: {auction['id']}")
    print(f"   Finaliza: {auction['endDate']}")
    if auction.get('bestBid'):
        eur = auction['bestBid']['amounts']['eurCents'] / 100
        bidder = auction['bestBid'].get('bidder', {}).get('nickname', '?')
        print(f"   Puja actual: {eur:.2f}€ (by {bidder})")
    return auction['id']


def _find_auction_by_card_slug(card_slug, headers):
    """
    Busca subasta activa por card slug (extraído de URL de market).
    El slug es tipo: francisco-roman-alarcon-suarez-2025-rare-79
    """
    # El card slug en la URL corresponde al campo 'slug' del card
    query = '''
    query GetCardBySlug($slug: String!) {
      football {
        card(slug: $slug) {
          assetId
          anyPlayer { displayName }
          anyTeam { name }
          latestEnglishAuction {
            id
            open
            endDate
            bestBid {
              amounts { eurCents }
              bidder { ... on User { nickname } }
            }
          }
        }
      }
    }
    '''
    data = graphql_request(query, {'slug': card_slug}, headers=headers)
    card = data.get('football', {}).get('card')
    if not card:
        # Fallback: intentar buscar por nombre parcial con allCards
        print(f"   ⚠️  No se encontró carta directa, intentando por nombre...")
        return _find_auction_by_card_slug_fallback(card_slug, headers)

    auction = card.get('latestEnglishAuction')
    if not auction or not auction.get('open'):
        print(f"❌ La carta de {card['anyPlayer']['displayName']} no tiene subasta activa")
        sys.exit(1)

    print(f"   ✅ {card['anyPlayer']['displayName']} ({card['anyTeam']['name']})")
    print(f"   Asset ID: {card['assetId']}")
    print(f"   Auction: {auction['id']}")
    print(f"   Finaliza: {auction['endDate']}")
    if auction.get('bestBid'):
        eur = auction['bestBid']['amounts']['eurCents'] / 100
        bidder = auction['bestBid'].get('bidder', {}).get('nickname', '?')
        print(f"   Puja actual: {eur:.2f}€ (by {bidder})")
    return auction['id']


def _find_auction_by_card_slug_fallback(card_slug, headers):
    """Fallback: extrae player slug del card slug y busca entre sus cartas."""
    import re
    # card_slug es tipo: francisco-roman-alarcon-suarez-2025-rare-79
    # Extraemos el nombre quitando el año-rareza-serial del final
    match = re.match(r'^(.+)-(\d{4})-(rare|super_rare|unique|limited)-(\d+)$', card_slug)
    if not match:
        print(f"❌ No se pudo parsear el slug de carta: {card_slug}")
        sys.exit(1)

    player_part = match.group(1)  # francisco-roman-alarcon-suarez
    year = int(match.group(2))
    serial = int(match.group(4))

    print(f"   Jugador: {player_part}, año: {year}, serial: #{serial}")
    # Buscar el jugador por slug parcial
    return _find_auction_by_player_slug(player_part, headers)


def _find_auction_by_player_slug(slug, headers):
    """Busca subasta rare activa de un jugador por su slug."""
    query = '''
    query GetPlayerCards($slug: String!) {
      football {
        player(slug: $slug) {
          displayName
          activeClub { name }
          cards(rarities: [rare], first: 20) {
            nodes {
              assetId
              seasonYear
              serialNumber
              latestEnglishAuction {
                id
                open
                endDate
                bestBid {
                  amounts { eurCents }
                  bidder { ... on User { nickname } }
                }
              }
            }
          }
        }
      }
    }
    '''
    data = graphql_request(query, {'slug': slug}, headers=headers)
    player_data = data.get('football', {}).get('player')
    if not player_data:
        print(f"❌ No se encontró jugador con slug: {slug}")
        sys.exit(1)

    print(f"   Jugador: {player_data['displayName']}")
    cards = player_data.get('cards', {}).get('nodes', [])
    active_auctions = [c for c in cards if c.get('latestEnglishAuction', {}).get('open')]

    if not active_auctions:
        print(f"❌ {player_data['displayName']} no tiene subastas rare activas ahora mismo")
        sys.exit(1)

    club = player_data.get('activeClub', {}).get('name', '?')
    if len(active_auctions) == 1:
        chosen = active_auctions[0]
    else:
        print(f"\n   ⚡ {len(active_auctions)} subastas activas de {player_data['displayName']}:")
        for i, c in enumerate(active_auctions):
            a = c['latestEnglishAuction']
            eur = a['bestBid']['amounts']['eurCents'] / 100 if a.get('bestBid') else 0
            bidder = a['bestBid']['bidder']['nickname'] if a.get('bestBid') and a['bestBid'].get('bidder') else '-'
            print(f"      {i+1}. #{c['serialNumber']} (season {c['seasonYear']}) — {eur:.2f}€ (by {bidder}) — fin: {a['endDate']}")
        active_auctions.sort(key=lambda c: c['latestEnglishAuction'].get('bestBid', {}).get('amounts', {}).get('eurCents', 0))
        chosen = active_auctions[0]
        print(f"   → Usando la más barata")

    auction = chosen['latestEnglishAuction']
    print(f"\n   ✅ {player_data['displayName']} ({club})")
    print(f"   Asset ID: {chosen['assetId']}")
    print(f"   Auction: {auction['id']}")
    print(f"   Finaliza: {auction['endDate']}")
    if auction.get('bestBid'):
        eur = auction['bestBid']['amounts']['eurCents'] / 100
        bidder = auction['bestBid'].get('bidder', {}).get('nickname', '?')
        print(f"   Puja actual: {eur:.2f}€ (by {bidder})")
    else:
        print(f"   Sin pujas aún")
    return auction['id']


def verify_auction(auction_id, headers):
    """Verifica que la subasta existe y está abierta."""
    query = '''
    query GetAuction($id: String!) {
      tokens {
        auction(id: $id) {
          id
          open
          endDate
          bestBid {
            amounts { eurCents }
            bidder { ... on User { nickname } }
          }
          anyCards {
            anyPlayer { displayName }
            anyTeam { name }
          }
        }
      }
    }
    '''
    raw_id = auction_id.replace("EnglishAuction:", "")
    data = graphql_request(query, {'id': raw_id}, headers=headers)
    auction = data['tokens']['auction']

    if not auction:
        print("❌ Subasta no encontrada")
        sys.exit(1)
    if not auction['open']:
        print("❌ La subasta ya no está abierta")
        sys.exit(1)

    card = auction['anyCards'][0]
    print(f"   Jugador: {card['anyPlayer']['displayName']}")
    print(f"   Equipo:  {card['anyTeam']['name'] if card['anyTeam'] else 'N/A'}")
    print(f"   Fin:     {auction['endDate']}")
    if auction.get('bestBid'):
        eur = auction['bestBid']['amounts']['eurCents'] / 100
        bidder = auction['bestBid'].get('bidder', {}).get('nickname', '?')
        print(f"   Puja actual: {eur:.2f}€ (by {bidder})")
    else:
        print(f"   Sin pujas aún")

    return auction


def check_auction_status(auction_id, headers):
    """Consulta el estado actual de la subasta. Devuelve (eur, bidder, open)."""
    query = '''
    query GetAuction($id: String!) {
      tokens {
        auction(id: $id) {
          open
          bestBid {
            amounts { eurCents }
            bidder { ... on User { nickname } }
          }
        }
      }
    }
    '''
    raw_id = auction_id.replace("EnglishAuction:", "")
    try:
        data = graphql_request(query, {'id': raw_id}, headers=headers)
        auction = data['tokens']['auction']
        if not auction:
            return None, None, False
        bid = auction.get('bestBid')
        if bid:
            eur = bid['amounts']['eurCents'] / 100
            bidder = bid.get('bidder', {}).get('nickname', '?')
            return eur, bidder, auction['open']
        return 0, None, auction['open']
    except Exception:
        return None, None, True  # en caso de error, asumir que sigue abierta


def wait_until(target_dt, auction_id=None, headers=None, my_nickname=None):
    """Espera hasta la hora objetivo mostrando cuenta atrás y estado de la subasta."""
    last_check = 0
    check_interval = 60  # revisar cada 60s
    current_bid_str = ""

    while True:
        now = time.time()
        remaining = (target_dt - now_spain()).total_seconds()
        if remaining <= 0:
            break

        # Revisar estado de la subasta periódicamente
        if auction_id and headers and (now - last_check) >= check_interval:
            eur, bidder, is_open = check_auction_status(auction_id, headers)
            last_check = now
            if not is_open:
                log(f"\n   ❌ La subasta se ha cerrado!")
                sys.exit(1)
            if eur and eur > 0:
                if bidder and my_nickname and bidder.lower() == my_nickname.lower():
                    current_bid_str = f" | 🟢 Ganando: {eur:.2f}€ (tú)"
                else:
                    current_bid_str = f" | 🔴 Perdiendo: {eur:.2f}€ (by {bidder})"
            else:
                current_bid_str = " | Sin pujas"

        if remaining > 60:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            status = f"   ⏳ Faltan {mins}m {secs}s{current_bid_str}"
        else:
            status = f"   ⏳ Faltan {remaining:.1f}s{current_bid_str}"

        print(f"\r{status}              ", end="", flush=True)

        # Sleep adaptativo: más preciso cuando queda poco
        if remaining > 60:
            time.sleep(10)
        elif remaining > 5:
            time.sleep(1)
        else:
            time.sleep(0.1)

    print(f"\r   🚀 ¡Hora alcanzada! Ejecutando puja...                                        ")


def log(msg):
    """Print con timestamp."""
    ts = now_spain().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def monitor_auction(auction_id, headers, my_nickname, poll_interval=15):
    """Monitoriza la subasta tras pujar hasta que se cierre. Muestra si vas ganando."""
    log("👁️  Monitorizando subasta hasta que termine...")
    log(f"   (Ctrl+C para dejar de monitorizar)\n")

    last_eur = None
    last_bidder = None

    while True:
        eur, bidder, is_open = check_auction_status(auction_id, headers)

        if not is_open:
            if bidder and my_nickname and bidder.lower() == my_nickname.lower():
                log(f"🏆 ¡SUBASTA GANADA! Has ganado con {eur:.2f}€")
            else:
                log(f"💀 Subasta terminada. Ganador: {bidder} con {eur:.2f}€")
            break

        # Solo imprimir si hay cambios
        if eur != last_eur or bidder != last_bidder:
            if bidder and my_nickname and bidder.lower() == my_nickname.lower():
                log(f"🟢 VAS GANANDO — {eur:.2f}€ (tú)")
            elif eur and eur > 0:
                log(f"🔴 TE HAN SUPERADO — {eur:.2f}€ (by {bidder})")
            last_eur = eur
            last_bidder = bidder

        time.sleep(poll_interval)


def execute_bid(auction_id, bid_cents):
    """Ejecuta la puja llamando al script de Node.js."""
    cmd = ['node', JS_SCRIPT, auction_id, str(bid_cents)]
    if USE_CREDIT:
        cmd.append('--use-credit')
    log(f"Comando: node pujar_carta.js {auction_id} {bid_cents}{' --use-credit' if USE_CREDIT else ''}")
    print()

    result = subprocess.run(cmd, cwd=os.path.dirname(JS_SCRIPT), capture_output=False)
    return result.returncode


def get_my_nickname(headers):
    """Obtiene el nickname del usuario actual."""
    query = '''
    query { currentUser { nickname } }
    '''
    try:
        data = graphql_request(query, headers=headers)
        return data.get('currentUser', {}).get('nickname')
    except Exception:
        return None


def daemonize(log_file):
    """Fork del proceso para ejecutarse en segundo plano con caffeinate."""
    # Lanzar caffeinate para evitar que el Mac duerma
    caffeinate_proc = subprocess.Popen(
        ['caffeinate', '-i', '-w', str(os.getpid())],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Fork
    pid = os.fork()
    if pid > 0:
        # Proceso padre: informar y salir
        print(f"\n🚀 Puja programada en segundo plano (PID: {pid})")
        print(f"   Log: {log_file}")
        print(f"   Cancelar: kill {pid}")
        # Guardar PID
        with open(PID_FILE, 'w') as f:
            f.write(str(pid))
        sys.exit(0)

    # Proceso hijo: nueva sesión
    os.setsid()

    # Redirigir stdout/stderr al log
    sys.stdout = open(log_file, 'w', buffering=1)
    sys.stderr = sys.stdout

    # Guardar PID del hijo
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    return caffeinate_proc


def main():
    parser = argparse.ArgumentParser(
        description='Programa una puja en Sorare a una hora de España',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 programar_puja.py EnglishAuction:767651d9-... 6.00 09:40
  python3 programar_puja.py EnglishAuction:767651d9-... 10 12:46:50
  python3 programar_puja.py 0x0400fdaf... 6.00 09:40

  # En segundo plano (sobrevive a cerrar terminal):
  python3 programar_puja.py --bg
  python3 programar_puja.py --bg EnglishAuction:... 10 14:30

  # Sin argumentos usa los valores DEFAULT_* del código:
  python3 programar_puja.py

  # Ver log de puja en background:
  tail -f output/puja_*.log

  # Cancelar puja programada:
  kill $(cat output/puja_programada.pid)
        """)
    parser.add_argument('identifier', nargs='?', default=None, help='Auction ID o Asset ID (o edita DEFAULT_IDENTIFIER)')
    parser.add_argument('euros', nargs='?', type=float, default=None, help='Euros a pujar (o edita DEFAULT_EUROS)')
    parser.add_argument('hora', nargs='?', default=None, help='Hora España HH:MM o HH:MM:SS (o edita DEFAULT_HORA)')
    parser.add_argument('--bg', action='store_true', help='Ejecutar en segundo plano (sobrevive a cerrar terminal)')
    parser.add_argument('--now', action='store_true', help='Pujar inmediatamente (ignora hora)')

    args = parser.parse_args()

    # Usar argumentos o defaults
    identifier = args.identifier or DEFAULT_IDENTIFIER
    euros = args.euros if args.euros is not None else DEFAULT_EUROS
    hora = args.hora or DEFAULT_HORA

    # NOW: desde argumento --now o variable global
    do_now = args.now or NOW

    # Validar
    if not identifier:
        print("❌ Falta el identifier. Pásalo como argumento o edita DEFAULT_IDENTIFIER en el código.")
        sys.exit(1)
    if not euros or euros <= 0:
        print("❌ Falta la cantidad. Pásala como argumento o edita DEFAULT_EUROS en el código.")
        sys.exit(1)
    if not do_now and not hora:
        print("❌ Falta la hora. Pásala como argumento, edita DEFAULT_HORA, o pon NOW = True.")
        sys.exit(1)

    bid_cents = int(round(euros * 100))
    target_time = None if do_now else parse_time_spain(hora)

    print("=" * 60)
    print("🎯 PUJA PROGRAMADA — SORARE")
    print("=" * 60)
    print(f"   Hora actual:  {now_spain().strftime('%H:%M:%S')} (España)")
    if do_now:
        print(f"   Modo:         AHORA")
    else:
        print(f"   Puja a las:   {target_time.strftime('%H:%M:%S')} (España)")
    print(f"   Cantidad:     {euros:.2f}€ ({bid_cents} céntimos)")
    print()

    # Resolver auction_id si es un asset_id
    headers = build_headers()
    auction_id = resolve_auction_id(identifier, headers)

    # Verificar subasta
    print(f"\n📋 Verificando subasta...")
    verify_auction(auction_id, headers)

    # Obtener mi nickname para saber si estoy ganando
    my_nickname = get_my_nickname(headers)
    if my_nickname:
        print(f"   Tu usuario: {my_nickname}")

    # Background mode (si NOW=True, no tiene sentido ir en background)
    caffeinate_proc = None
    if (args.bg or BG) and not do_now:
        auction_short = auction_id.replace('EnglishAuction:', '')[:8]
        log_file = os.path.join(OUTPUT_DIR, f'puja_{auction_short}.log')
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        caffeinate_proc = daemonize(log_file)
        log("Proceso en segundo plano iniciado")
        log(f"Auction: {auction_id}")
        if do_now:
            log(f"Puja: {euros:.2f}€ AHORA")
        else:
            log(f"Puja: {euros:.2f}€ a las {target_time.strftime('%H:%M:%S')}")

    # Esperar si no es NOW
    if not do_now:
        remaining = (target_time - now_spain()).total_seconds()
        print(f"\n⏰ Esperando {int(remaining)}s hasta las {target_time.strftime('%H:%M:%S')}...")
        if not args.bg:
            print(f"   (Ctrl+C para cancelar)\n")
        try:
            wait_until(target_time, auction_id=auction_id, headers=headers, my_nickname=my_nickname)
        except KeyboardInterrupt:
            print("\n\n❌ Puja cancelada por el usuario")
            sys.exit(0)
    else:
        print()

    # Ejecutar puja
    exit_code = execute_bid(auction_id, bid_cents)

    if exit_code == 0:
        log(f"✅ Puja ejecutada a las {now_spain().strftime('%H:%M:%S')}")
        # Check rápido: ¿voy ganando?
        time.sleep(2)
        eur, bidder, is_open = check_auction_status(auction_id, headers)
        if eur and bidder:
            if my_nickname and bidder.lower() == my_nickname.lower():
                log(f"🟢 VAS GANANDO — {eur:.2f}€ (tú)")
            else:
                log(f"🔴 TE HAN SUPERADO — {eur:.2f}€ (by {bidder})")
    else:
        log(f"❌ Error al pujar (exit code: {exit_code})")

    # Limpiar
    if caffeinate_proc:
        caffeinate_proc.terminate()
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
