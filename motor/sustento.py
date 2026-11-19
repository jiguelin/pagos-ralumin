# -*- coding: utf-8 -*-
"""
motor/sustento.py — Interpreta la columna SUSTENTO 1 del Sheet de bancos.

Ejemplos reales:
  PAGO VILLAFE PARTS EIRL FAC. E001-29 $ 4,320,87; COMISION DEL BANCO $ 1,53; COM. RETENCION E001-1030 S/. 450,23
  PAGO IIPS GMBH INVOICE 61138 $ 9,586,37; COMISION DEL BANCO $ 89,22; INVOICE  61143 $ 513,63; COMISIÓN DEL BANCO $ 4,78
  PAGO DHL EXPRESS PERU SAC FAC. F162-00001893 S/. 620,96            (factura en soles pagada desde la cuenta en dólares)
  PAGO HECTORIR & CO SAC (F001-15171)
  CALLE 23 PAGO XIO LIFT ASCENSORES DEL PERU SAC FAC. F001-384 (CUOTA 2)
  ADELANTO BLINCE INDUSTRY (HK) LIMITED INVOICE CH2026081402  $ 100,00; COMISION DEL BANCO $ 69,00----> (IMPO PENDIENTE DE LLEGADA)

La retención ("COM. RETENCIÓN E001-1030 S/. 450,23") es INFORMATIVA: no forma parte del pago; sirve para validar
que pago + retención = saldo de la factura. La comisión del banco sí es parte del cargo del banco (cuenta 6391094).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from motor.banco import clave
from ralumin import config as cfg


@dataclass
class Referencia:
    texto: str              # como aparece: 'E001-29', '61138', 'CH2026081402'
    tipo_hint: str          # 'FAC', 'RH', 'REC', 'NC', 'INV', 'LIQ', ''
    monto: float | None     # monto escrito en el sustento
    moneda: str             # 'USD' / 'PEN' / '' (moneda del monto escrito)


@dataclass
class Retencion:
    numero: str             # comprobante de retención E001-1030
    soles: float


@dataclass
class Interpretacion:
    tipo: str               # 'pago' / 'adelanto' / 'fuera' / 'sin sustento'
    motivo: str = ''
    proveedor_texto: str = ''
    referencias: list[Referencia] = field(default_factory=list)
    comision: float = 0.0
    retenciones: list[Retencion] = field(default_factory=list)
    centro_costo: str = ''
    parcial: bool = False


_NUM = r'\d[\d.,]*\d|\d'


def numero_texto(s: str) -> float | None:
    """'4,320,87' → 4320.87 · '9,840,16' → 9840.16 · '1177,70' → 1177.70 · '1,000.00' → 1000.00 · '7,60,94' → 760.94.
    En el Sheet la última coma o punto seguido de 2 dígitos es el decimal; los demás separadores se ignoran."""
    s = str(s or '').strip().rstrip('.,;')
    m = re.match(r'^([\d.,]*?)[.,](\d{2})$', s) or re.match(r'^([\d.,]*?\d[.,]\d{3})[.,](\d)$', s)
    if m:
        entero = re.sub(r'[.,]', '', m.group(1)) or '0'
        return float(f'{entero}.{m.group(2)}')
    s2 = re.sub(r'[.,]', '', s)
    return float(s2) if s2.isdigit() else None


_RE_RET = re.compile(r'COM\.?\s*R\w*CI[OÓ]N\s+([A-Z]\d{3}\s*-\s*\d+)\s*(?:S/\.?|S/,)?\s*(' + _NUM + ')', re.I)
_RE_COM = re.compile(r'COMISI\w*\s+(?:DEL?\s+)?BANCO\s*(\$|S/\.?)?\s*(' + _NUM + ')', re.I)
_RE_DOC = re.compile(r'(?<![A-Z0-9-])((?:[A-Z]-\d{3}|[A-Z]{1,4}\d{0,3}|\d{3,4})-\d{1,13})(?![A-Z0-9])')
_RE_INV = re.compile(r'(?:INVOICE|PROFORMA(?:\s+INVOICE)?|PRES\.|N°|Nº|NRO\.?)\s*\$?\s*([A-Z0-9][A-Z0-9/\-]*\d[A-Z0-9/\-]*)', re.I)
_RE_MONTO = re.compile(r'(\$|S/\.?|S/,)\s*(' + _NUM + ')')
_TIPOS = [('FACTURA', 'FAC'), ('FAC', 'FAC'), ('RH', 'RH'), ('REC', 'REC'), ('NC', 'NC'), ('LIQ', 'LIQ'), ('COC', 'LIQ'),
          ('INVOICE', 'INV'), ('PROFORMA', 'INV')]
_CORTE_PROV = re.compile(r'\s\.?(?:FAC\b|FAC\.|FACTURA|RH\b|REC\.|REC\b|NC\b|INVOICE|PROFORMA|PRES\.|LIQ\.|COC\.|RECIBO|\$|S/|\(|'
                         r'[A-Z]{1,4}\d{0,3}-\d|\d{3,4}-\d|;)', re.I)


def _moneda(simbolo: str) -> str:
    return 'USD' if simbolo.startswith('$') else 'PEN'


def interpretar(sustento: str, detalle: str = '') -> Interpretacion:
    s = ' '.join(str(sustento or '').split())
    u = s.upper()
    cc = next((v for k, v in cfg.CENTROS_COSTO.items() if k in u), '')
    if not s:
        # sin sustento: solo es "fuera" si el detalle del banco dice claramente que es un gasto bancario
        d = ' '.join(str(detalle or '').upper().split())
        for patron, motivo in cfg.FUERA_DE_ALCANCE:
            if patron in d or d.startswith(('COMIS', 'IMPUESTO', 'ITF', 'MANT')):
                return Interpretacion('fuera', motivo if patron in d else 'Gasto bancario', centro_costo=cc)
        return Interpretacion('sin sustento', 'Falta el sustento en la columna I del Sheet', centro_costo=cc)
    # ---- fuera de alcance
    for patron, motivo in cfg.FUERA_DE_ALCANCE:
        if patron in u and not re.search(r'\bFAC\.|\bFACTURA\b|\bINVOICE\b|\bRH\b', u):
            return Interpretacion('fuera', motivo, centro_costo=cc)
    es_adelanto = u.startswith('ADELANTO') or ' ADELANTO ' in f' {u} '[:40]
    if not (es_adelanto or 'PAGO' in u or _RE_DOC.search(u)):
        # no dice PAGO ni cita documentos ("4TO. ABONO SERNA DIAZ…"): no se descarta, queda para revisar
        return Interpretacion('sin sustento', 'El sustento no dice qué se pagó (no menciona PAGO ni documentos)', centro_costo=cc,
                              proveedor_texto=re.sub(r'^\S*\s*(ABONO|TRANSFERENCIA|DEPOSITO)\s+', '', u).strip())
    it = Interpretacion('adelanto' if es_adelanto else 'pago', centro_costo=cc, parcial='PARCIAL' in u)
    # ---- retenciones (informativas) y comisión: se sacan del texto antes de buscar facturas
    for m in _RE_RET.finditer(u):
        it.retenciones.append(Retencion(re.sub(r'\s', '', m.group(1)), numero_texto(m.group(2)) or 0.0))
    u2 = _RE_RET.sub(' ', u)
    for m in _RE_COM.finditer(u2):
        it.comision += numero_texto(m.group(2)) or 0.0
    it.comision = round(it.comision, 2)
    u2 = _RE_COM.sub(' ', u2)
    u2 = re.sub(r'-{2,}>.*$', ' ', u2)                    # "-----------> (IMPO PENDIENTE DE LLEGADA)"
    # ---- proveedor: lo que sigue a PAGO / ADELANTO hasta la primera referencia
    m = re.search(r'(?:PAGO(?:\s+DE)?(?:\s+IMPUESTO(?:\s+DE)?)?|ADELANTO)\s+(.*)', u2)
    resto = m.group(1) if m else u2
    c = _CORTE_PROV.search(' ' + resto)
    it.proveedor_texto = (resto[:c.start() - 1] if c else resto).strip(' ,;.-')
    # ---- referencias por tramo (separados por ';')
    for tramo in re.split(r';', u2):
        montos = [(_moneda(a), numero_texto(b)) for a, b in _RE_MONTO.findall(tramo)]
        refs = []
        for mm in _RE_DOC.finditer(tramo):
            txt = mm.group(1)
            if re.fullmatch(r'\d{4}-\d{2}', txt) or re.fullmatch(r'20\d{2}-\d{1,2}', txt):   # periodos "2026/08"
                continue
            antes = tramo[max(0, mm.start() - 22):mm.start()]
            hint = next((h for k, h in _TIPOS if k in antes), '')
            refs.append((mm.start(), txt, hint))
        for mm in _RE_INV.finditer(tramo):
            txt = mm.group(1).strip('-/')
            if any(abs(mm.start(1) - p) < 3 or txt in t for p, t, _ in refs):
                continue
            refs.append((mm.start(1), txt, 'INV'))
        refs.sort()
        for i, (_, txt, hint) in enumerate(refs):
            mon, monto = (montos[i] if len(refs) == len(montos) else (montos[0] if len(refs) == 1 and montos else ('', None)))
            it.referencias.append(Referencia(txt, hint, monto, mon))
    # misma referencia repetida (p. ej. "(F001-15171)" y "FAC. F001-15171"): una sola
    unicas, vistas = [], set()
    for r in it.referencias:
        k = clave(r.texto)
        if k not in vistas:
            vistas.add(k)
            unicas.append(r)
    it.referencias = unicas
    return it
