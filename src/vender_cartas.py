import pandas as pd
import subprocess
import sys
from pathlib import Path


def process_excel_and_sell_cards(excel_file, js_script_path="vender_carta.js"):
    """
    Lee un archivo Excel y ejecuta el script JavaScript de venta por cada fila.

    Args:
        excel_file: Ruta al archivo Excel
        js_script_path: Ruta al script JavaScript
    """

    # Verificar que el archivo Excel existe
    if not Path(excel_file).exists():
        print(f"❌ Error: No se encuentra el archivo {excel_file}")
        sys.exit(1)

    # Verificar que el script JS existe
    if not Path(js_script_path).exists():
        print(f"❌ Error: No se encuentra el script {js_script_path}")
        sys.exit(1)

    # Leer el archivo Excel
    print(f"📖 Leyendo archivo: {excel_file}")
    try:
        df = pd.read_excel(excel_file)
    except Exception as e:
        print(f"❌ Error al leer el Excel: {e}")
        sys.exit(1)

    # Verificar que tiene las columnas necesarias
    required_columns = ['name', 'seasonYear', 'assetId', 'price']
    missing_columns = [col for col in required_columns if col not in df.columns]

    if missing_columns:
        print(f"❌ Error: Faltan las columnas: {', '.join(missing_columns)}")
        print(f"   Columnas encontradas: {', '.join(df.columns)}")
        sys.exit(1)

    total_rows = len(df)
    print(f"✅ Se encontraron {total_rows} cartas para procesar\n")

    # Procesar cada fila
    success_count = 0
    error_count = 0

    for idx, row in df.iterrows():
        card_num = idx + 1
        name = row['name']
        asset_id = row['assetId']
        price_euros = float(row['price'])
        price_cents = int(price_euros * 100)  # Convertir euros a céntimos

        print(f"{'=' * 70}")
        print(f"🎴 Procesando carta {card_num}/{total_rows}")
        print(f"   Nombre: {name}")
        print(f"   Asset ID: {asset_id}")
        print(f"   Precio: {price_euros}€ ({price_cents} céntimos)")
        print(f"{'=' * 70}")

        # Ejecutar el script de Node.js
        try:
            command = ['node', js_script_path, asset_id, str(price_cents)]

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30  # timeout de 30 segundos
            )

            # Mostrar la salida del script
            if result.stdout:
                print("📤 Salida:")
                print(result.stdout)

            if result.returncode == 0:
                print(f"✅ Carta {card_num} procesada con éxito")
                success_count += 1
            else:
                print(f"❌ Error al procesar carta {card_num}")
                if result.stderr:
                    print("Error details:")
                    print(result.stderr)
                error_count += 1

        except subprocess.TimeoutExpired:
            print(f"⏱️ Timeout: La carta {card_num} tardó más de 30 segundos")
            error_count += 1

        except Exception as e:
            print(f"❌ Excepción al procesar carta {card_num}: {e}")
            error_count += 1

        print()  # Línea en blanco entre cartas

    # Resumen final
    print(f"\n{'=' * 70}")
    print(f"📊 RESUMEN FINAL")
    print(f"{'=' * 70}")
    print(f"✅ Cartas procesadas con éxito: {success_count}")
    print(f"❌ Cartas con errores: {error_count}")
    print(f"📋 Total: {total_rows}")
    print(f"{'=' * 70}")


def main():
    """Función principal"""

    excel_file = "../output/prueba.xlsx"
    js_script = "../javascript/vender_carta.js"

    print("🚀 Iniciando procesamiento de cartas...")
    print(f"   Excel: {excel_file}")
    print(f"   Script JS: {js_script}\n")

    process_excel_and_sell_cards(excel_file, js_script)


if __name__ == "__main__":
    main()