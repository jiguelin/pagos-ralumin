# -*- coding: utf-8 -*-
"""
motor/exportar.py — Arma el Excel para "Importación de Pagos" de NewContaSis (plantilla FORMATO_PAGOS, columnas A–W).

Reglas validadas con importaciones reales (18/11/2026):
  · El archivo es un libro LIMPIO solo con filas de datos: sin las 13 filas de instrucciones y sin filas vacías con
    formato (NewContaSis las cuenta como registros inválidos).
  · NewContaSis agrupa las filas por documento de pago (B + C) y arma UNA línea de la cuenta de pago (104 / 401141)
    sumando la columna I de esas filas → I lleva el importe de CADA línea, nunca el total repetido.
  · No bloquea asientos descuadrados: aquí se valida que la suma de cada pago cuadre antes de exportar.
  · Numera los asientos ordenando por B y luego por C: el Excel sale en ese orden para saber qué 04-N tocó a cada pago.
  · B/C = tipo y número de la primera factura pagada. Si esa factura ya se usó como B/C en otra operación (del mismo
    archivo o de un lote anterior), la operación lleva B = 13 y C = n° de operación, para no fusionar dos cargos del banco.
  · Retenciones: B = 20, C = n° del comprobante de retención, G = 401141; un asiento por comprobante.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime

COLUMNAS = list('ABCDEFGHIJKLMNOPQRSTUVW')
TITULOS = ['Fecha cancelación', 'Documento', 'Número', 'Tipo doc. proveedor', 'N° doc. proveedor', 'Razón social',
           'Cuenta de pago', 'Moneda', 'Importe línea', 'Tipo de cambio', 'Doc. cancelado', 'Serie-número',
           'Fecha documento', 'Fecha vencimiento', 'RUC documento', 'Razón social documento', 'Importe S/',
           'Importe US$', 'Cuenta documento', 'Medio de pago', 'Glosa', 'Centro de costo 1', 'Centro de costo 2']


@dataclass
class Asiento:
    """Un documento de pago de la plantilla = un asiento en NewContaSis."""
    ref: str                       # id del movimiento del banco o n° de retención
    fecha: date
    b: str
    c: str
    tipo_prov: str
    ruc: str
    nombre: str
    cuenta_pago: str
    moneda: str                    # 'D' / 'S'
    tc: float
    medio: str
    glosa: str
    centro_costo: str = ''
    lineas: list[dict] = field(default_factory=list)   # dicts con K..S: tipo, numero, fecha_doc, fecha_venc, ruc, nombre, monto, soles, dolares, cuenta

    def filas(self) -> list[list]:
        out = []
        for l in self.lineas:
            out.append([self.fecha, self.b, self.c, self.tipo_prov, self.ruc, self.nombre[:60], self.cuenta_pago, self.moneda,
                        round(l['monto'], 2), self.tc, l['tipo'], l['numero'], l['fecha_doc'] or self.fecha,
                        l['fecha_venc'] or l['fecha_doc'] or self.fecha, l['ruc'], (l['nombre'] or '')[:60],
                        round(l['soles'], 2), round(l['dolares'], 2), l['cuenta'], self.medio, self.glosa[:60],
                        self.centro_costo, ''])
        return out

    def errores(self) -> list[str]:
        e = []
        if not self.lineas:
            e.append('sin líneas')
        for l in self.lineas:
            exp = l['dolares'] if self.moneda == 'D' else l['soles']
            if abs(round(l['monto'], 2) - round(exp, 2)) > 0.005:
                e.append(f"{l['numero']}: la columna I ({l['monto']:.2f}) no coincide con {'R' if self.moneda == 'D' else 'Q'} ({exp:.2f})")
            if abs(round(l['dolares'] * self.tc, 2) - round(l['soles'], 2)) > 0.02 and self.moneda == 'D':
                e.append(f"{l['numero']}: Q no es R × TC")
        for campo, v in (('fecha', self.fecha), ('B', self.b), ('C', self.c), ('RUC', self.ruc), ('cuenta de pago', self.cuenta_pago), ('TC', self.tc)):
            if not v:
                e.append(f'falta {campo}')
        if len(self.c) > 20:
            e.append('C tiene más de 20 caracteres')
        return e


def asientos_de_pagos(propuestas, usados_bc: set[tuple] | None = None) -> tuple[list[Asiento], set[tuple]]:
    """Convierte propuestas 'listo' en asientos aplicando la regla de B/C. Devuelve (asientos, B/C usados)."""
    from motor.cruce import tipo_doc_identidad
    usados = set(usados_bc or set())
    ops_c: set[str] = {c for b, c in usados if b == '13'}
    out = []
    for p in sorted(propuestas, key=lambda x: (x.mov.fecha, x.mov.fila)):
        primera = next((l for l in p.lineas if not l.es_comision), None)
        if primera is None:
            continue
        bc = (primera.tipo, primera.numero)
        if bc in usados:
            op = ''.join(ch for ch in p.mov.operacion if ch.isdigit()) or p.mov.fecha.strftime('%d%m%Y')
            c, n = op, 1
            while c in ops_c:
                n += 1
                c = f'{op}-{n}'
            ops_c.add(c)
            bc = ('13', c)
        usados.add(bc)
        a = Asiento(ref=p.mov.id, fecha=p.mov.fecha, b=bc[0], c=bc[1], tipo_prov=tipo_doc_identidad(p.ruc), ruc=p.ruc,
                    nombre=p.proveedor, cuenta_pago=p.cuenta, moneda=p.moneda, tc=p.tc, medio=p.medio, glosa=p.glosa,
                    centro_costo=p.centro_costo)
        for l in p.lineas:
            a.lineas.append(dict(tipo=l.tipo, numero=l.numero, fecha_doc=l.fecha_doc, fecha_venc=l.fecha_venc, ruc=l.ruc,
                                 nombre=l.nombre, monto=l.monto, soles=l.soles, dolares=l.dolares, cuenta=l.cuenta,
                                 moneda_doc=l.doc.moneda if l.doc else ''))
        out.append(a)
    return out, usados


def ordenar(asientos: list[Asiento]) -> list[Asiento]:
    """Orden en que NewContaSis numera los asientos al importar (observado: por B y luego por C)."""
    return sorted(asientos, key=lambda a: (a.b, a.c))


def excel_importacion(asientos: list[Asiento]) -> bytes:
    """Libro limpio, una hoja, solo filas de datos, en el orden de numeración."""
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'FORMATO_PAGOS'
    fuente = Font(name='Arial', size=8)
    r = 1
    for a in ordenar(asientos):
        for fila in a.filas():
            for j, (col, v) in enumerate(zip(COLUMNAS, fila)):
                c = ws.cell(row=r, column=j + 1, value=(v if v != '' else None))
                c.font = fuente
                if col in 'AMN':
                    if isinstance(c.value, datetime):
                        pass
                    elif isinstance(c.value, date):
                        c.value = datetime(c.value.year, c.value.month, c.value.day)
                    c.number_format = 'dd/mm/yyyy'
                elif col == 'J':
                    c.number_format = '0.0000'
                elif col in 'IQR':
                    c.number_format = '0.00'
                else:
                    c.number_format = '@'
            r += 1
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def excel_revision(asientos: list[Asiento], extra: list[dict] | None = None) -> bytes:
    """Mismo contenido con títulos y una columna de asiento previsto, para leerlo cómodo (no se importa)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'REVISION'
    ws.append(['Asiento (orden)'] + TITULOS)
    for c in ws[1]:
        c.font = Font(name='Arial', size=9, bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1F3B5C')
    for n, a in enumerate(ordenar(asientos), start=1):
        for fila in a.filas():
            ws.append([n] + [v if v != '' else None for v in fila])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name='Arial', size=9)
            if isinstance(c.value, (datetime, date)):
                c.number_format = 'dd/mm/yyyy'
    if extra:
        ws2 = wb.create_sheet('OBSERVACIONES')
        cols = list(extra[0].keys())
        ws2.append(cols)
        for d in extra:
            ws2.append([d.get(k) for k in cols])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()
