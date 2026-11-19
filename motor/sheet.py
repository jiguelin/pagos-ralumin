# -*- coding: utf-8 -*-
"""
motor/sheet.py — Escribe el asiento (04-N) en la columna de control del Google Sheet al confirmar un lote.

Google exige una credencial para escribir aunque el Sheet esté abierto a "cualquiera con el enlace": se usa una
cuenta de servicio (GOOGLE_SERVICE_ACCOUNT en los secrets, el JSON que entrega Google Cloud) y el Sheet se comparte
con su correo como Editor. Sin esa credencial, el app ofrece la lista fila → asiento para pegarla a mano.
"""
from __future__ import annotations


def credencial_disponible(secreto) -> bool:
    return bool(secreto)


def escribir_asientos(info_cuenta: dict, sheet_id: str, items: list[dict]) -> int:
    """items: dicts con pestana, fila, col_asiento, asiento. Solo escribe celdas vacías. Devuelve celdas escritas."""
    import gspread
    from google.oauth2.service_account import Credentials
    cred = Credentials.from_service_account_info(dict(info_cuenta), scopes=['https://www.googleapis.com/auth/spreadsheets'])
    libro = gspread.authorize(cred).open_by_key(sheet_id)
    escritas = 0
    por_hoja: dict[str, list[dict]] = {}
    for it in items:
        if it.get('pestana') and it.get('fila') and it.get('asiento'):
            por_hoja.setdefault(it['pestana'], []).append(it)
    for nombre, lista in por_hoja.items():
        ws = libro.worksheet(nombre)
        celdas = []
        for it in lista:
            actual = ws.cell(it['fila'], it['col_asiento']).value
            if actual and str(actual).strip():
                continue                                    # nunca pisa lo que el equipo ya escribió
            celdas.append(gspread.Cell(it['fila'], it['col_asiento'], it['asiento']))
        if celdas:
            ws.update_cells(celdas, value_input_option='RAW')
            escritas += len(celdas)
    return escritas
