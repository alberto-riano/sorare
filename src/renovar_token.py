import requests
import bcrypt
import os
import re
import json
import base64
import time

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'config.txt')

def read_config():
    """Lee las variables del archivo config.txt"""
    config = {}
    with open(config_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
                config[key] = value
    return config


def get_token_status(token=None):
    """Devuelve el estado del JWT actual leyendo su payload (claim `exp`).

    Resultado: {"valid": bool, "expired": bool, "expires_at": int|None,
                "seconds_left": int|None}
    No verifica la firma (solo decodifica el payload para informar en la UI).
    """
    if token is None:
        token = read_config().get('JWT_TOKEN', '')

    if not token:
        return {"valid": False, "expired": True, "expires_at": None, "seconds_left": None}

    try:
        payload_b64 = token.split('.')[1]
        padding = '=' * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = int(payload.get('exp'))
    except Exception:
        return {"valid": False, "expired": True, "expires_at": None, "seconds_left": None}

    now = int(time.time())
    seconds_left = exp - now
    expired = seconds_left <= 0
    return {
        "valid": not expired,
        "expired": expired,
        "expires_at": exp,
        "seconds_left": seconds_left,
    }


def update_token_in_config(new_token):
    """Actualiza el JWT_TOKEN en el archivo config.txt manteniendo el resto de variables"""
    with open(config_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Reemplazar el valor de JWT_TOKEN
    updated_content = re.sub(
        r'JWT_TOKEN=.*',
        f'JWT_TOKEN={new_token}',
        content
    )

    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(updated_content)
    print(f"Token actualizado en {config_path}")


graphql_url = 'https://api.sorare.com/graphql'


def get_salt(email):
    print(f"Obteniendo salt para {email}...")
    resp = requests.get(f'https://api.sorare.com/api/v1/users/{email}', timeout=30)
    resp.raise_for_status()
    salt = resp.json()['salt'].encode()
    print("Salt recibido:", salt)
    return salt


def hash_password(password, salt):
    print("Hasheando contraseña...")
    hashed = bcrypt.hashpw(password.encode(), salt).decode()
    print("Contraseña hasheada:", hashed)
    return hashed


def sign_in(input_data):
    print("Haciendo llamada signIn con input:", input_data)
    query = '''
    mutation SignInMutation($input: signInInput!) {
      signIn(input: $input) {
        currentUser {
          slug
        }
        jwtToken(aud: "myapp") {
          token
          expiredAt
        }
        otpSessionChallenge
        errors {
          message
        }
      }
    }
    '''
    variables = {"input": input_data}
    headers = {'content-type': 'application/json'}
    resp = requests.post(graphql_url, json={'query': query, 'variables': variables}, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()['data']['signIn']


# ---------------------------------------------------------------------------
# API reutilizable (CLI + Web)
# ---------------------------------------------------------------------------

def start_login(email=None, password=None):
    """Primer paso del login.

    Devuelve un dict con una de estas formas:
      - {"status": "mfa_required", "otp_session_challenge": str}
      - {"status": "success", "token": str, "expired_at": str}
      - {"status": "error", "message": str}
    """
    if email is None or password is None:
        config = read_config()
        email = email or config.get('EMAIL')
        password = password or config.get('PASSWORD')

    if not email or not password:
        return {"status": "error", "message": "EMAIL o PASSWORD no encontrados en config.txt"}

    salt = get_salt(email)
    hashed_password = hash_password(password, salt)

    response = sign_in({"email": email, "password": hashed_password})

    errors = response.get('errors') or []
    if errors:
        if any(error.get('message') == '2fa_missing' for error in errors):
            otp_session = response.get('otpSessionChallenge')
            if otp_session:
                return {"status": "mfa_required", "otp_session_challenge": otp_session}
            return {"status": "error", "message": "No se obtuvo otpSessionChallenge para 2FA."}
        messages = "; ".join(e.get('message', '') for e in errors)
        return {"status": "error", "message": messages or "Error de login desconocido."}

    if response.get('currentUser'):
        jwt = response.get('jwtToken') or {}
        return {
            "status": "success",
            "token": jwt.get('token'),
            "expired_at": jwt.get('expiredAt'),
        }

    return {"status": "error", "message": "Login fallido sin usuario y sin error conocido."}


def complete_login_with_otp(otp_session_challenge, otp_code):
    """Segundo paso del login con el codigo MFA.

    Devuelve un dict:
      - {"status": "success", "token": str, "expired_at": str}
      - {"status": "error", "message": str}
    """
    if not otp_session_challenge:
        return {"status": "error", "message": "Falta el otpSessionChallenge."}
    if not otp_code:
        return {"status": "error", "message": "Falta el codigo MFA."}

    response = sign_in({
        "otpSessionChallenge": otp_session_challenge,
        "otpAttempt": str(otp_code).strip(),
    })

    errors = response.get('errors') or []
    if errors:
        messages = "; ".join(e.get('message', '') for e in errors)
        return {"status": "error", "message": messages or "Error al validar el codigo MFA."}

    if response.get('currentUser'):
        jwt = response.get('jwtToken') or {}
        return {
            "status": "success",
            "token": jwt.get('token'),
            "expired_at": jwt.get('expiredAt'),
        }

    return {"status": "error", "message": "No se obtuvo token despues de 2FA. Revisa el codigo MFA."}


def main():
    result = start_login()

    if result["status"] == "error":
        print("Error en login:", result["message"])
        return

    if result["status"] == "success":
        print("Login exitoso sin 2FA")
        print("Token:", result["token"])
        print("Expira en:", result["expired_at"])
        update_token_in_config(result["token"])
        return

    # status == mfa_required
    print("2FA activado, otpSessionChallenge:", result["otp_session_challenge"])
    otp_code = input("Introduce el codigo OTP (6 digitos) de tu autenticador 2FA: ").strip()
    result2 = complete_login_with_otp(result["otp_session_challenge"], otp_code)

    if result2["status"] == "success":
        print("Login exitoso con 2FA!")
        print("Token:", result2["token"])
        print("Expira en:", result2["expired_at"])
        update_token_in_config(result2["token"])
    else:
        print("Error en 2FA:", result2["message"])


if __name__ == "__main__":
    main()