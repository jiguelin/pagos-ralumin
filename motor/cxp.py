# -*- coding: utf-8 -*-
"""
motor/cxp.py — Lee el "Análisis de documentos pendientes" de la cuenta 4212 exportado de NewContaSis.

Formato recomendado: "en moneda original" (columnas MONTO S/. · SALDO x D. · MONTO US$. · SALDO x D. · M.* · T/C).
También acepta el formato solo en soles (MONTO · SALDO · M.* · T/C): ahí el saldo en dólares se calcula como
monto ÷ T/C de cada movimiento.

Estructura: una fila de encabezado por proveedor ("20613892801 CONSORCIO GEHLA E.I.R.L." o "RUC - NOMBRE") y debajo
sus movimientos (provisión negativa, pagos y retenciones positivos). Un documento puede tener varias filas:
el saldo pendiente es la suma por (proveedor, tipo, número).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from motor.banco import clave


@dataclass
class Documento:
    ruc: str
    proveedor: str
    tipo: str               # 01, 91, 14…
    numero: str             # tal cual la 4212: 00E001-0000000000029
    fecha_doc: date | None
    fecha_venc: date | None
    moneda: str             # 'D' / 'S'
    saldo_usd: float        # negativo = se debe
    saldo_pen: float
    cuenta: str = '4212'
    glosa: str = ''
    movimientos: int = 0

    @property
    def serie(self) -> str:
        return self.numero.split('-')[0] if '-' in self.numero else ''

    @property
    def correlativo(self) -> str:
        return self.numero.split('-', 1)[1] if '-' in self.numero else self.numero

    @property
    def deuda(self) -> float:
        """Lo que falta pagar, en la moneda del documento (positivo)."""
        return round(-(self.saldo_usd if self.moneda == 'D' else self.saldo_pen), 2)

    @property
    def clave_doc(self) -> str:
        return clave_documento(self.numero)


def clave_documento(numero: str) -> str:
    """Serie y número comparables sin importar los ceros: '00E001-0000000000029' y 'E001-29' → 'E001-29'.
    Invoices: '000INV-0000000061186', 'INV-61186' → 'INV-61186'."""
    s = str(numero or '').upper().replace(' ', '')
    if '-' in s:
        serie, num = s.split('-', 1)
    else:
        serie, num = '', s
    serie = serie.lstrip('0') or ('0' if serie else '')
    num = re.sub(r'[^A-Z0-9]', '', num)
    num = num.lstrip('0') or '0'
    return f'{serie}-{num}'


def _f(v) -> float:
    if v is None or v == '':
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(',', '').strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _d(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def leer_4212(data: bytes) -> tuple[list[Documento], dict]:
    return leer_pendientes(data)


def leer_pendientes(data: bytes) -> tuple[list[Documento], dict]:
    """Análisis de documentos pendientes de CUALQUIER cuenta por pagar (4212, 4111, 4170, 40…).
    La cuenta sale del título del reporte ("4212 EMITIDAS - Facturas…") o de la columna de cuenta de cada fila.
    Devuelve (documentos con saldo, info). Incluye documentos con saldo a favor (adelantos) con deuda negativa."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    filas = [list(r) for r in ws.iter_rows(values_only=True)]
    titulo = ' '.join(str(v) for f in filas[:8] for v in f if v)
    m = re.search(r'AL MES DE ([A-Z]+)', titulo.upper())
    info = {'mes': m.group(1).title() if m else '', 'moneda_original': False, 'cuenta': ''}
    for f in filas[:8]:
        for v in f:
            mm = re.match(r'^\s*(\d{2,7})\s+[A-ZÁÉÍÓÚ]', str(v or ''))
            if mm and not info['cuenta']:
                info['cuenta'] = mm.group(1)
                info['titulo_cuenta'] = ' '.join(str(v).split())
    col: dict[str, int] = {}
    prov = ('', '')
    acc: dict[tuple, dict] = {}
    for fila in filas:
        k = [clave(v) for v in fila]
        if 'MES' in k and 'NUMERO' in k:                       # cabecera de columnas
            idx_num = [j for j, x in enumerate(k) if x == 'NUMERO']
            col = {'mes': k.index('MES'), 'tipo': idx_num[0] - 1, 'numero': idx_num[0]}
            for j, x in enumerate(k):
                if x == 'FASIENT':
                    col['f_asiento'] = j
                elif x == 'FECHAV':
                    col['f_venc'] = j
                elif x == 'FECHAD':
                    col['f_doc'] = j
                elif x == 'GLOSA':
                    col['glosa'] = j
                elif x.startswith('MONTOUS'):
                    col['usd'] = j
                    info['moneda_original'] = True
                elif x.startswith('MONTO'):
                    col.setdefault('pen', j)
                elif x == 'M':
                    col['moneda'] = j
                elif x == 'TC':
                    col['tc'] = j
            continue
        if not col:
            continue
        primero = next((v for v in fila if v not in (None, '')), None)
        j0 = next((j for j, v in enumerate(fila) if v not in (None, '')), None)
        # encabezado de proveedor: un texto con el RUC/código al inicio, en la columna MES, sin más datos
        if isinstance(primero, str) and j0 == col['mes'] and re.match(r'^\s*\d{8,11}\s*-?\s*[A-Za-z&]', primero):
            mm = re.match(r'^\s*(\d{8,11})\s*-?\s*(.*)$', primero)
            prov = (mm.group(1), ' '.join(mm.group(2).split()))
            continue
        mes = fila[col['mes']] if col['mes'] < len(fila) else None
        if not (isinstance(mes, str) and re.fullmatch(r'\d{2}', mes.strip())):
            continue
        tipo = str(fila[col['tipo']] or '').strip().zfill(2)
        numero = str(fila[col['numero']] or '').strip()
        if not numero:
            continue
        g = lambda kk: fila[col[kk]] if kk in col and col[kk] < len(fila) else None
        moneda = str(g('moneda') or '').strip().upper()[:1] or 'S'
        pen = _f(g('pen'))
        tc = _f(g('tc'))
        usd = _f(g('usd')) if 'usd' in col else (round(pen / tc, 2) if tc else 0.0)
        cta_fila = str(fila[col['mes'] - 1] or '').strip() if col['mes'] >= 1 else ''
        cuenta = cta_fila if re.fullmatch(r'\d{2,7}', cta_fila) else (info['cuenta'] or '4212')
        kk = (prov[0], tipo, numero, cuenta)
        d = acc.setdefault(kk, dict(ruc=prov[0], proveedor=prov[1], tipo=tipo, numero=numero, fecha_doc=_d(g('f_doc')),
                                    fecha_venc=_d(g('f_venc')), moneda=moneda, usd=0.0, pen=0.0, glosas=[], n=0, cuenta=cuenta))
        d['usd'] += usd
        d['pen'] += pen
        d['n'] += 1
        gl = ' '.join(str(g('glosa') or '').split())
        if gl and gl not in d['glosas']:
            d['glosas'].append(gl)
        if moneda in ('D', 'S') and d['moneda'] not in ('D', 'S'):
            d['moneda'] = moneda
    docs = [Documento(ruc=d['ruc'], proveedor=d['proveedor'], tipo=d['tipo'], numero=d['numero'], fecha_doc=d['fecha_doc'],
                      fecha_venc=d['fecha_venc'], moneda=d['moneda'] if d['moneda'] in ('D', 'S') else 'S',
                      saldo_usd=round(d['usd'], 2), saldo_pen=round(d['pen'], 2), glosa=' | '.join(d['glosas'])[:200],
                      movimientos=d['n'], cuenta=d['cuenta']) for d in acc.values()]
    docs = [x for x in docs if abs(x.deuda) >= 0.01]
    info['documentos'] = len(docs)
    info['proveedores'] = len({x.ruc for x in docs})
    info['cuentas'] = sorted({x.cuenta for x in docs})
    return docs, info


def leer_entidades(data: bytes) -> dict[str, str]:
    """Reporte 'Clientes - Proveedores (todos)' de NewContaSis → {RUC/código: razón social}."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = {}
    for fila in wb.worksheets[0].iter_rows(values_only=True):
        vals = [v for v in fila if v not in (None, '')]
        if len(vals) >= 2 and isinstance(vals[0], str) and re.fullmatch(r'\d{8,11}', vals[0].strip()):
            nombre = ' '.join(str(vals[1]).split())
            if nombre and nombre.upper() != 'ANULADO':
                out[vals[0].strip()] = nombre
    return out
