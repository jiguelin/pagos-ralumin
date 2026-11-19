# -*- coding: utf-8 -*-
"""
motor/banco.py — Lee el Google Sheet "PAGOS RALUMIN" (una pestaña por banco y mes) y devuelve los movimientos.

Cada pestaña tiene unas filas de título y luego una fila de cabecera con FECHA, DETALLE, N° OPERACIÓN, CARGO / ABONO…
El orden de las columnas cambia de una pestaña a otra, por eso se ubican por su título:

  · la columna que sigue a CARGO / ABONO es la del asiento ("CUENTA", "ASIENTO", "ASIENTOS" o sin título): si tiene
    cualquier valor, el movimiento YA está registrado en NewContaSis;
  · entre esa columna y CUENTA (ABC) puede haber otra "CUENTA" con el número de cuenta bancaria;
  · CUENTA (ABC) trae la letra de la cuenta (A, B, C, D);
  · SUSTENTO 1 dice qué se pagó.

Montos: "$2.782,44", "-$1.019,00", "S/ -1.140,00", "-S/ 1,000.00", "S/ 99.00"…  Fechas: "1 de septiembre de 2026".
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

MESES = {'ENERO': 1, 'FEBRERO': 2, 'MARZO': 3, 'ABRIL': 4, 'MAYO': 5, 'JUNIO': 6, 'JULIO': 7, 'AGOSTO': 8,
         'SETIEMBRE': 9, 'SEPTIEMBRE': 9, 'OCTUBRE': 10, 'NOVIEMBRE': 11, 'DICIEMBRE': 12}
MESES_CORTOS = {'ENE': 1, 'FEB': 2, 'MAR': 3, 'ABR': 4, 'MAY': 5, 'MAYO': 5, 'JUN': 6, 'JUL': 7, 'AGO': 8, 'AGOS': 8,
                'SEP': 9, 'SEPT': 9, 'SET': 9, 'SETI': 9, 'OCT': 10, 'NOV': 11, 'DIC': 12}


def clave(v) -> str:
    """Texto comparable: sin tildes, mayúsculas, solo A–Z y 0–9."""
    s = unicodedata.normalize('NFKD', str(v or '')).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9]', '', s)


@dataclass
class Movimiento:
    pestana: str
    banco: str              # BCP / BBVA / SCOTIA
    fila: int               # fila en la hoja (1 = primera), para escribir la columna del asiento
    col_asiento: int        # columna (1 = A) del asiento en esa pestaña
    fecha: date | None
    detalle: str
    operacion: str
    monto: float            # negativo = egreso
    moneda_texto: str       # 'USD' / 'PEN' / '' según el símbolo del monto
    asiento: str            # lo que dice la columna del asiento ('' = no registrado)
    nro_cuenta: str
    letra: str
    sustento: str
    texto_monto: str = ''
    id: str = field(default='')

    def __post_init__(self):
        if not self.id:
            base = '|'.join([clave(self.pestana), self.fecha.isoformat() if self.fecha else '', self.operacion.strip(),
                             f'{self.monto:.2f}', clave(self.sustento)[:120], clave(self.detalle)[:60]])
            self.id = hashlib.sha1(base.encode()).hexdigest()[:16]

    @property
    def es_egreso(self) -> bool:
        return self.monto < 0

    @property
    def registrado(self) -> bool:
        return bool(self.asiento.strip())


# ----------------------------------------------------------------------------- utilidades
def leer_monto(v) -> tuple[float | None, str]:
    """Devuelve (monto, moneda) desde el texto del Sheet. None si la celda no es un monto."""
    if isinstance(v, (int, float)):
        return float(v), ''
    s = str(v or '').strip()
    if not s:
        return None, ''
    up = s.upper().replace(' ', '')
    moneda = 'PEN' if 'S/' in up else ('USD' if '$' in up else '')
    neg = '-' in s
    num = re.sub(r'[^0-9,.]', '', s)
    if not num or not re.search(r'\d', num):
        return None, moneda
    if ',' in num and '.' in num:
        num = num.replace('.', '').replace(',', '.') if num.rfind(',') > num.rfind('.') else num.replace(',', '')
    elif ',' in num:
        num = num.replace(',', '.') if re.search(r',\d{1,2}$', num) else num.replace(',', '')
    elif num.count('.') > 1:
        num = num.replace('.', '')
    elif re.search(r'\.\d{3}$', num) and moneda == '':
        pass
    try:
        x = float(num)
    except ValueError:
        return None, moneda
    return (-x if neg else x), moneda


def leer_fecha(v, anio_defecto: int | None = None) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or '').strip()
    if not s:
        return None
    m = re.search(r'(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+(?:de(?:l)?\s+)?(\d{4})', s, re.I)
    if m:
        mes = MESES.get(clave(m.group(2)))
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(1)))
            except ValueError:
                return None
    m = re.search(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})', s)
    if m:
        a = int(m.group(3))
        a = a + 2000 if a < 100 else a
        try:
            return date(a, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def info_pestana(nombre: str) -> tuple[str, int | None, int | None]:
    """'BCP-SEPT -26' → ('BCP', 2026, 9). Pestañas antiguas sin banco ('AGO-25') → ('', 2025, 8)."""
    k = nombre.upper().replace(' ', '')
    banco = 'BCP' if 'BCP' in k else 'BBVA' if 'BBVA' in k else 'SCOTIA' if 'SCOTIA' in k else ''
    m = re.search(r'([A-Z]{3,5})-?(\d{2})$', k)
    if not m:
        return banco, None, None
    mes = MESES_CORTOS.get(m.group(1)) or MESES_CORTOS.get(m.group(1)[:3])
    return banco, 2000 + int(m.group(2)), mes


# ----------------------------------------------------------------------------- lectura
def _cabecera(fila: list) -> dict[str, int] | None:
    k = [clave(v) for v in fila]
    if 'FECHA' not in k or not any(x.startswith('CARGO') for x in k):
        return None
    col = {'fecha': k.index('FECHA')}
    for j, x in enumerate(k):
        if x.startswith('DETALLE'):
            col.setdefault('detalle', j)
        elif x.startswith('NOPERACION') or x.startswith('NROOPERACION') or x == 'OPERACION' or x.startswith('NUMEROOPERACION'):
            col.setdefault('operacion', j)
        elif x.startswith('CARGO'):
            col.setdefault('monto', j)
        elif x.startswith('CUENTAABC'):
            col.setdefault('letra', j)
        elif x.startswith('SUSTENTO'):
            col.setdefault('sustento', j)
    if 'monto' not in col:
        return None
    col['asiento'] = col['monto'] + 1
    if 'letra' in col and col['letra'] - col['asiento'] >= 2:
        col['nro_cuenta'] = col['letra'] - 1
    return col


def _filas_hoja(ws) -> list[list]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def leer_pestana(nombre: str, filas: list[list]) -> list[Movimiento]:
    banco, anio, _ = info_pestana(nombre)
    out: list[Movimiento] = []
    col = None
    for i, fila in enumerate(filas, start=1):
        if col is None:
            col = _cabecera(fila)
            continue
        g = lambda k: fila[col[k]] if k in col and col[k] < len(fila) else None
        monto, moneda = leer_monto(g('monto'))
        if monto is None or monto == 0:
            continue
        f = leer_fecha(g('fecha'), anio)
        if f is None:
            continue
        txt = lambda v: '' if v is None else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)).strip()
        out.append(Movimiento(pestana=nombre, banco=banco, fila=i, col_asiento=col['asiento'] + 1, fecha=f,
                              detalle=txt(g('detalle')), operacion=txt(g('operacion')), monto=round(monto, 2),
                              moneda_texto=moneda, asiento=txt(g('asiento')), nro_cuenta=txt(g('nro_cuenta')),
                              letra=txt(g('letra')).upper(), sustento=' '.join(txt(g('sustento')).split()),
                              texto_monto=txt(g('monto'))))
    # dos movimientos idénticos (mismo día, operación, monto y texto): el segundo lleva #2, el tercero #3…
    vistos: dict[str, int] = {}
    for m in out:
        n = vistos.get(m.id, 0) + 1
        vistos[m.id] = n
        if n > 1:
            m.id = f'{m.id}#{n}'
    return out


def leer_libro(data: bytes) -> dict[str, list[list]]:
    """Excel del Sheet completo (descargado o exportado por la URL) → {pestaña: filas}."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    return {ws.title: _filas_hoja(ws) for ws in wb.worksheets}


def pestanas_con_bancos(libro: dict[str, list[list]]) -> list[str]:
    """Pestañas con banco y mes reconocibles, de la más reciente a la más antigua."""
    res = []
    for n in libro:
        b, a, m = info_pestana(n)
        if b and a and m:
            res.append((a, m, n))
    return [n for a, m, n in sorted(res, key=lambda t: (-t[0], -t[1], t[2]))]


def descargar_sheet(sheet_id: str, timeout: int = 60) -> bytes:
    """Descarga el Sheet completo como .xlsx (funciona si está compartido 'cualquiera con el enlace')."""
    import urllib.request
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    if not data.startswith(b'PK'):
        raise ValueError('Google no devolvió un Excel. Revise que el Sheet esté compartido como "cualquiera con el enlace".')
    return data
