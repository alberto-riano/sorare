#!/usr/bin/env python3
"""
Lee output/cartas_para_vender.xlsx, filtra las cartas marcadas con "Sí"
en la columna "Vender" y ejecuta la venta de cada una usando
javascript/vender_carta.js.
"""
import os
import sys
import subprocess
import openpyxl

EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'output', 'cartas_para_vender.xlsx')
JS_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '..', 'javascript', 'vender_carta.js')

COL_JUGADOR = 1
COL_VENDER = 11
COL_PRECIO = 12
COL_ASSET = 13


def main():
    if not os.path.isfile(EXCEL_PATH):
        print(f"❌ No se encuentra {EXCEL_PATH}")
        sys.exit(1)
    if not os.path.isfile(JS_SCRIPT):
        print(f"❌ No se encuentra {JS_SCRIPT}")
        sys.exit(1)

    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    ws = wb.active

    ventas = []
    for row in range(2, ws.max_row + 1):
        vender = (ws.cell(row=row, column=COL_VENDER).value or '').strip()
        if vender.lower() not in ('sí', 'si', 'yes'):
            continue
        precio = ws.cell(row=row, column=COL_PRECIO).value
        asset_id = ws.cell(row=row, column=COL_ASSET).value
        nombre = ws.cell(row=row, column=COL_JUGADOR).value or '?'

        if not asset_id:
            print(f"⚠️  Fila {row}: sin assetId, saltando")
            continue
        if not precio:
            print(f"⚠️  {nombre}: sin precio de venta, saltando")
            continue
        try:
            precio_eur = float(precio)
        except (ValueError, TypeError):
            print(f"⚠️  {nombre}: precio inválido '{precio}', saltando")
            continue

        ventas.append({
            'row': row,
            'name': nombre,
            'asset_id': str(asset_id).strip(),
            'price_eur': precio_eur,
            'price_cents': int(precio_eur * 100),
        })

    if not ventas:
        print("ℹ️  No hay cartas marcadas con 'Sí' para vender")
        return

    print(f"🔍 {len(ventas)} carta(s) marcadas para vender:\n")
    for v in ventas:
        print(f"  • {v['name']}  →  {v['price_eur']:.2f} €")

    print()
    respuesta = input("¿Confirmar ventas? (s/n): ").strip().lower()
    if respuesta not in ('s', 'si', 'sí', 'y', 'yes'):
        print("❌ Cancelado")
        return

    ok = 0
    fail = 0
    for v in ventas:
        print(f"\n{'='*60}")
        print(f"🎴 {v['name']}  —  {v['price_eur']:.2f} € ({v['price_cents']} cents)")
        print(f"   assetId: {v['asset_id']}")

        try:
            result = subprocess.run(
                ['node', JS_SCRIPT, v['asset_id'], str(v['price_cents'])],
                capture_output=True, text=True, timeout=30,
            )
            if result.stdout:
                print(result.stdout)
            if result.returncode == 0:
                print(f"✅ Vendida")
                ok += 1
            else:
                print(f"❌ Error (código {result.returncode})")
                if result.stderr:
                    print(result.stderr)
                fail += 1
        except subprocess.TimeoutExpired:
            print("⏱️  Timeout")
            fail += 1
        except Exception as e:
            print(f"❌ {e}")
            fail += 1

    print(f"\n{'='*60}")
    print(f"📊 Resultado: {ok} vendidas, {fail} errores, {len(ventas)} total")


if __name__ == '__main__':
    main()
