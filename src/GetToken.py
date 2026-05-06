import requests
import bcrypt
import os
import re

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


def main():
    # Leer email y password del archivo config.txt
    config = read_config()
    email = config.get('EMAIL')
    password = config.get('PASSWORD')

    if not email or not password:
        print("Error: EMAIL o PASSWORD no encontrados en config.txt")
        return

    print(f"Usando email: {email}")

    salt = get_salt(email)
    hashed_password = hash_password(password, salt)

    # Primera llamada con email y password hasheada
    input_data = {
        "email": email,
        "password": hashed_password
    }
    response1 = sign_in(input_data)

    if response1.get('errors'):
        print("Errores en login:", response1['errors'])
        if any(error['message'] == '2fa_missing' for error in response1['errors']):
            otp_session = response1.get('otpSessionChallenge')
            if otp_session:
                print("2FA activado, otpSessionChallenge:", otp_session)
                # Aquí pedimos el código OTP por teclado
                otp_code = input("Introduce el código OTP (6 dígitos) de tu autenticador 2FA: ").strip()
                input_data_2fa = {
                    "otpSessionChallenge": otp_session,
                    "otpAttempt": otp_code
                }
                response2 = sign_in(input_data_2fa)
                if response2.get('errors'):
                    print("Errores en 2FA:", response2['errors'])
                    return
                if response2.get('currentUser'):
                    print("Login exitoso con 2FA!")
                    print("Usuario:", response2['currentUser']['slug'])
                    token = response2['jwtToken']['token']
                    print("Token:", token)
                    print("Expira en:", response2['jwtToken']['expiredAt'])

                    # Actualizar token en config.txt
                    update_token_in_config(token)
                    return
                else:
                    print("No se obtuvo token después de 2FA. Revisa el código OTP.")
            else:
                print("No se obtuvo otpSessionChallenge para 2FA.")
        else:
            return
    elif response1.get('currentUser'):
        print("Login exitoso sin 2FA")
        print("Usuario:", response1['currentUser']['slug'])
        token = response1['jwtToken']['token']
        print("Token:", token)
        print("Expira en:", response1['jwtToken']['expiredAt'])

        # Actualizar token en config.txt
        update_token_in_config(token)
    else:
        print("Login fallido sin usuario y sin error conocido.")


if __name__ == "__main__":
    main()