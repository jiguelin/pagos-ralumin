# -*- coding: utf-8 -*-
"""
motor/cruce.py — Cruza los egresos del banco con la 4212 y propone cada pago.

Estados:
  registrado · la columna del asiento del Sheet ya tiene valor (o la app ya lo registró en un lote)
  fuera      · no es pago a proveedor de la 4212 (ITF, planilla, SUNAT, caja chica, transferencias…)
  adelanto   · adelanto de importación (se trata aparte)
  revisar    · no cuadra, no se encontró la factura o el proveedor, falta TC…
  listo      · cruzado y cuadrado: se puede poner en un lote
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from motor.banco import Movimiento, clave
from motor.cxp import Documento, clave_documento
from motor.sustento import Interpretacion, interpretar
from ralumin import config as cfg

_SUFIJOS = r'\b(S\s?A\s?C|E\s?I\s?R\s?L|S\s?R\s?L|S\s?A\s?A|S\s?A|LTDA?|LIMITED|LIMITEDC|LIMITE|CO|INC|LLC|GMBH|SPA|SAS|' \
           r'SOCIEDAD ANONIMA CERRADA|SOCIEDAD ANONIMA|SOCIEDAD COMERCIAL DE RESPONSABILIDAD LIMITADA|SUCURSAL|DEL|DE|LA|EL|Y|THE)\b'


def nombre_base(n: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFKD', str(n or '')).encode('ascii', 'ignore').decode().upper()
    s = re.sub(r'[.,&()\-/]', ' ', s)
    s = re.sub(_SUFIJOS, ' ', s)
    return ' '.join(s.split())


_GENERICAS = {'PERU', 'SERVICIOS', 'SERVICIO', 'GRUPO', 'CORPORACION', 'INDUSTRIAL', 'INDUSTRIALES', 'COMERCIAL', 'INVERSIONES',
              'EMPRESA', 'NEGOCIOS', 'CONSORCIO', 'PARTS', 'IMPORT', 'IMPORTADORA', 'TRADING', 'LOGISTICS', 'EXPRESS', 'INTERNATIONAL',
              'ADUANAS', 'MINERA', 'MINERIA', 'TECNICA', 'GENERALES', 'PAGO', 'SOLUCIONES', 'INGENIERIA', 'TECNOLOGIA', 'MACHINERY',
              'INDUSTRY', 'TECHNOLOGY', 'SUMINISTROS', 'DISTRIBUCIONES', 'CONTRATISTAS', 'REPRESENTACIONES', 'MAQUINARIAS', 'EQUIPOS'}


def parecido(a: str, b: str) -> float:
    a, b = nombre_base(a), nombre_base(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    cont = 1.0 if (a in b or b in a) and min(len(a), len(b)) >= 4 else 0.0
    # una palabra distintiva en común (GESTEMIND, SCHARFF…) acerca, pero no basta para cruzar sola (< 0.72)
    distintiva = 0.7 if any(len(t) >= 5 and t not in _GENERICAS for t in ta & tb) else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), jac, 0.92 * cont, distintiva)


def tipo_doc_identidad(ruc: str) -> str:
    r = (ruc or '').strip()
    if len(r) == 11 and r[:2] in ('10', '15', '16', '17', '20'):
        return '6'
    if len(r) == 8 and r.isdigit() and not r.startswith('000'):
        return '1'
    return '0'


def clave_numero(texto: str) -> str:
    """Solo la parte del número, alfanumérica y sin ceros a la izquierda (para invoices: '61138', 'CH2026081402')."""
    s = clave_documento(texto)
    num = s.split('-', 1)[1] if '-' in s else s
    return num


@dataclass
class Linea:
    tipo: str               # K
    numero: str             # L
    fecha_doc: date | None  # M
    fecha_venc: date | None  # N
    ruc: str                # O
    nombre: str             # P
    monto: float            # I: importe de la línea en la moneda del pago
    soles: float            # Q
    dolares: float          # R
    cuenta: str             # S
    doc: Documento | None = None
    es_comision: bool = False


@dataclass
class RetencionInfo:
    numero: str
    soles: float
    en_moneda_doc: float
    nota: str = ''


@dataclass
class Propuesta:
    mov: Movimiento
    estado: str
    motivos: list[str] = field(default_factory=list)
    interp: Interpretacion | None = None
    cuenta: str = ''        # 104xxx
    moneda: str = ''        # 'D' / 'S' de la cuenta bancaria
    tc: float | None = None
    tc_fecha: date | None = None
    ruc: str = ''
    proveedor: str = ''
    lineas: list[Linea] = field(default_factory=list)
    retenciones: list[RetencionInfo] = field(default_factory=list)
    centro_costo: str = ''
    medio: str = ''
    glosa: str = ''
    notas: list[str] = field(default_factory=list)

    @property
    def importe(self) -> float:
        return round(-self.mov.monto, 2)

    @property
    def documentos(self) -> str:
        return ', '.join(clave_documento(l.numero) for l in self.lineas if not l.es_comision)

    @property
    def comision(self) -> float:
        return round(sum(l.monto for l in self.lineas if l.es_comision), 2)


def cuenta_bancaria(m: Movimiento) -> tuple[str, str] | None:
    if m.nro_cuenta in cfg.CUENTAS_POR_NUMERO:
        c, mon, _ = cfg.CUENTAS_POR_NUMERO[m.nro_cuenta]
        return c, mon
    return cfg.CUENTAS_POR_LETRA.get((m.banco, m.letra))


class Indice4212:
    def __init__(self, docs: list[Documento], aplicado: dict[tuple, float] | None = None):
        self.docs = [d for d in docs if d.deuda > 0.05]          # residuos de diferencia de cambio no cuentan
        self.por_clave: dict[str, list[Documento]] = {}
        self.por_numero: dict[str, list[Documento]] = {}
        self.por_ruc: dict[str, list[Documento]] = {}
        self.nombres: dict[str, str] = {}
        for d in self.docs:
            self.por_clave.setdefault(d.clave_doc, []).append(d)
            self.por_numero.setdefault(clave_numero(d.numero), []).append(d)
            self.por_ruc.setdefault(d.ruc, []).append(d)
            self.nombres.setdefault(d.ruc, d.proveedor)
        # lo ya usado (lotes anteriores que la 4212 cargada todavía no refleja + lo propuesto en esta corrida)
        self.usado: dict[tuple, float] = dict(aplicado or {})

    @staticmethod
    def k(d: Documento) -> tuple:
        return (d.ruc, d.tipo, d.numero)

    def disponible(self, d: Documento) -> float:
        return round(d.deuda - self.usado.get(self.k(d), 0.0), 2)

    def usar(self, d: Documento, monto_doc: float):
        self.usado[self.k(d)] = round(self.usado.get(self.k(d), 0.0) + monto_doc, 2)

    def mejor_proveedor(self, texto: str) -> tuple[str, float]:
        mejor, puntaje = '', 0.0
        for ruc, nom in self.nombres.items():
            p = parecido(texto, nom)
            if p > puntaje:
                mejor, puntaje = ruc, p
        return mejor, puntaje

    def buscar(self, ref_texto: str, ruc: str = '', nombre: str = '', hint: str = '') -> list[Documento]:
        """Documentos pendientes que corresponden a la referencia. Nunca se acepta un documento de un proveedor cuyo
        nombre no se parece al del sustento: muchas empresas usan la misma serie (E001-29 existe en varios proveedores)."""
        cands = list(self.por_clave.get(clave_documento(ref_texto), [])) if '-' in ref_texto else []
        if not cands and ('-' not in ref_texto or hint == 'INV'):      # invoices del exterior: solo el número
            cands = list(self.por_numero.get(clave_numero(ref_texto), []))
        if not cands and ruc:                                          # "N° 17" de una factura de exportación
            cands = [d for d in self.por_ruc.get(ruc, []) if clave_numero(d.numero) == clave_numero(ref_texto)]
        if ruc:
            cands = [d for d in cands if d.ruc == ruc] or [d for d in cands if nombre and parecido(nombre, d.proveedor) >= 0.85]
        elif nombre:
            cands = [d for d in cands if parecido(nombre, d.proveedor) >= 0.8]
        if len({d.ruc for d in cands}) > 1 and nombre:
            ruc2 = max(((d.ruc, parecido(nombre, d.proveedor)) for d in cands), key=lambda t: t[1])[0]
            cands = [d for d in cands if d.ruc == ruc2]
        return cands


def _medio(m: Movimiento) -> str:
    u = (m.detalle or '').upper()
    return cfg.MEDIO_EXTERIOR if ('EXT' in u and 'TRANSF' in u) or 'AB.TR.EXT' in u else cfg.MEDIO_TRANSFERENCIA


def _glosa(p: Propuesta) -> str:
    docs = p.documentos
    nom = p.proveedor or (p.interp.proveedor_texto if p.interp else '')
    g = f'PAGO {nom[:30].strip()} {docs}'.strip()
    return g[:60]


def proponer(movs: list[Movimiento], docs: list[Documento], historial_tc, aplicado: dict | None = None,
             registrados_app: set[str] | None = None, en_lote: set[str] | None = None,
             bancos: dict | None = None, memoria=None) -> list[Propuesta]:
    """Una propuesta por egreso, en orden de fecha. `aplicado` = montos ya usados por documento (lotes que la 4212
    cargada no refleja). `registrados_app` / `en_lote` = ids de movimientos que la app ya registró / puso en un lote."""
    idx = Indice4212(docs, aplicado)
    registrados_app = registrados_app or set()
    en_lote = en_lote or set()
    out: list[Propuesta] = []
    egresos = sorted([m for m in movs if m.es_egreso], key=lambda m: (m.fecha or date.min, m.fila))
    for m in egresos:
        p = Propuesta(mov=m, estado='listo', medio=_medio(m))
        firma = f'{m.pestana.strip()}|{m.fecha.isoformat()}|{m.operacion.strip()}|{-m.monto:.2f}'
        if m.registrado or m.id in registrados_app or firma in registrados_app:
            p.estado = 'registrado'
            p.motivos.append(f'Asiento {m.asiento}' if m.asiento else 'Registrado por la app')
            out.append(p)
            continue
        if m.id in en_lote or firma in en_lote:
            p.estado = 'en lote'
            out.append(p)
            continue
        it = interpretar(m.sustento, m.detalle)
        p.interp = it
        p.centro_costo = it.centro_costo
        sol = memoria.solucion(m.id, firma) if memoria else None
        pat = memoria.patron(m.sustento) if (memoria and not sol) else None
        if it.tipo == 'fuera' and not (sol or pat):
            p.estado = 'fuera'
            p.motivos.append(it.motivo)
            out.append(p)
            continue
        cb = cuenta_bancaria(m)
        if not cb:
            p.estado = 'revisar'
            p.motivos.append(f'Cuenta bancaria sin configurar ({m.banco} {m.letra} {m.nro_cuenta})')
            out.append(p)
            continue
        p.cuenta, p.moneda = cb
        if it.tipo == 'adelanto' and not sol:
            p.estado = 'adelanto'
            p.motivos.append('Adelanto de importación: se registra aparte')
            out.append(p)
            continue
        t, exacto = historial_tc.buscar(m.fecha) if historial_tc is not None else (None, False)
        if not t:
            p.estado = 'revisar'
            p.motivos.append(f'Falta el tipo de cambio SUNAT del {m.fecha:%d/%m/%Y} (subir el PDF del mes)')
            out.append(p)
            continue
        p.tc, p.tc_fecha = t.venta, t.fecha
        if not exacto:
            p.notas.append(f'TC del {t.fecha:%d/%m} (sin publicación el {m.fecha:%d/%m})')
        bcos = bancos or cfg.RUC_BANCO
        if sol:
            _cruzar(p, _interp_de(it, sol['nombre'], sol['lineas'], sol.get('comision', 0.0), p.moneda), idx, bcos,
                    memoria, ruc_forzado=sol['ruc'])
            p.notas.insert(0, 'Solución guardada por el equipo' + (' (sugerida por IA)' if sol.get('origen') == 'ia' else ''))
        else:
            _cruzar(p, it if it.tipo == 'pago' else _interp_de(it, it.proveedor_texto, [], it.comision, p.moneda), idx, bcos, memoria)
            if p.estado == 'revisar' and pat:
                lineas = _lineas_por_patron(pat, p, it, idx)
                if lineas:
                    notas = list(p.notas)
                    _reiniciar(p)
                    p.notas = notas
                    _cruzar(p, _interp_de(it, pat['nombre'], lineas, it.comision, p.moneda), idx, bcos, memoria,
                            ruc_forzado=pat['ruc'])
                    p.notas.insert(0, f'Patrón aprendido ({"saldo igual al pago" if pat["modo"] == "monto" else "documentos más antiguos"})')
        p.glosa = _glosa(p)
        out.append(p)
    return out


def _cruzar(p: Propuesta, it: Interpretacion, idx: Indice4212, bancos: dict, memoria=None, ruc_forzado: str = ''):
    m = p.mov
    total = p.importe
    com = it.comision
    neto = round(total - com, 2)
    # ---- proveedor y documentos: solución/patrón del equipo → alias aprendido → parecido de nombres
    alias = memoria.ruc_alias(it.proveedor_texto) if (memoria and not ruc_forzado) else ''
    if ruc_forzado or alias:
        ruc_guess, punt = ruc_forzado or alias, 1.0
        if alias:
            p.notas.append('Proveedor reconocido por lo aprendido')
    else:
        ruc_nombre, punt = idx.mejor_proveedor(it.proveedor_texto) if it.proveedor_texto else ('', 0.0)
        ruc_guess = ruc_nombre if punt >= 0.72 else ''
    encontrados: list[tuple] = []          # (ref, doc)
    faltan = []
    for r in it.referencias:
        cands = idx.buscar(r.texto, ruc_guess, '' if ruc_forzado else it.proveedor_texto, r.tipo_hint)
        if len(cands) == 1:
            encontrados.append((r, cands[0]))
        elif len(cands) > 1:
            p.motivos.append(f'{r.texto}: hay {len(cands)} documentos con ese número')
            faltan.append(r)
        else:
            faltan.append(r)
    rucs = {d.ruc for _, d in encontrados}
    if len(rucs) > 1:
        p.estado = 'revisar'
        p.motivos.append('Paga documentos de varios proveedores en una sola operación')
        return
    p.ruc = next(iter(rucs)) if rucs else ruc_guess
    p.proveedor = idx.nombres.get(p.ruc, it.proveedor_texto)
    if encontrados and ruc_guess and p.ruc != ruc_guess and punt >= 0.9:
        p.notas.append(f'El texto dice "{it.proveedor_texto}" pero la factura es de {p.proveedor}')
    if faltan:
        p.estado = 'revisar'
        for r in faltan:
            if not any(r.texto in x for x in p.motivos):
                p.motivos.append(f'{r.texto} no está pendiente en la 4212 (¿ya pagada, al contado o sin provisionar?)')
    if not it.referencias:
        p.estado = 'revisar'
        p.motivos.append('El sustento no indica la factura')
        if p.ruc:
            exactas = [d for d in idx.por_ruc.get(p.ruc, []) if abs(_en_moneda_doc(neto, p, d) - idx.disponible(d)) <= cfg.TOL_SALDO]
            if len(exactas) == 1:
                p.notas.append(f'Sugerencia: {exactas[0].numero} tiene saldo igual al pago')
    if not p.ruc:
        p.estado = 'revisar'
        p.motivos.append(f'Proveedor no encontrado en la 4212: "{it.proveedor_texto}"')
    if p.estado == 'revisar':
        return
    # ---- montos por documento (en la moneda de la cuenta)
    montos: list[float | None] = []
    for r, d in encontrados:
        mismo = (r.moneda == 'USD' and p.moneda == 'D') or (r.moneda == 'PEN' and p.moneda == 'S')
        montos.append(r.monto if (r.monto is not None and mismo) else None)
    if len(encontrados) == 1:
        if montos[0] is not None and abs(montos[0] - neto) > cfg.TOL_CUADRE:
            p.notas.append(f'El sustento dice {montos[0]:,.2f} pero el banco menos comisión da {neto:,.2f}: se usa el banco')
        montos = [neto]
    elif any(x is None for x in montos):
        resto = round(neto - sum(x for x in montos if x is not None), 2)
        for i, (r, d) in enumerate(encontrados):
            if montos[i] is None:
                disp = _de_moneda_doc(idx.disponible(d), p, d)
                montos[i] = round(min(disp, resto), 2)
                resto = round(resto - montos[i], 2)
        if resto > cfg.TOL_CUADRE:
            montos[-1] = round(montos[-1] + resto, 2)
    suma = round(sum(montos) + com, 2)
    if abs(suma - total) > cfg.TOL_CUADRE:
        p.estado = 'revisar'
        p.motivos.append(f'No cuadra: facturas {sum(montos):,.2f} + comisión {com:,.2f} = {suma:,.2f}, banco {total:,.2f}')
        return
    # ---- líneas y saldos
    for (r, d), monto in zip(encontrados, montos):
        q, rr = _q_r(monto, p)
        pago_doc = rr if d.moneda == 'D' else q
        disp = idx.disponible(d)
        if pago_doc > disp + cfg.TOL_SALDO:
            p.estado = 'revisar'
            p.motivos.append(f'{d.numero}: el pago ({pago_doc:,.2f}) supera el saldo pendiente ({disp:,.2f})')
        if r.moneda and r.monto is not None and not ((r.moneda == 'USD') == (p.moneda == 'D')):
            esperado = r.monto
            obtenido = q if r.moneda == 'PEN' else rr
            if abs(esperado - obtenido) > max(0.05, 0.002 * esperado):
                p.notas.append(f'{d.numero}: el sustento dice {esperado:,.2f} y al TC {p.tc} resulta {obtenido:,.2f}')
        p.lineas.append(Linea(tipo=d.tipo, numero=d.numero, fecha_doc=d.fecha_doc, fecha_venc=d.fecha_venc or d.fecha_doc,
                              ruc=d.ruc, nombre=d.proveedor, monto=round(monto, 2), soles=q, dolares=rr,
                              cuenta=d.cuenta or cfg.CUENTA_PROVEEDORES, doc=d))
    # ---- retención informativa: pago + retención no debe pasar del saldo
    if it.retenciones and encontrados:
        d0 = encontrados[0][1]
        pago0 = p.lineas[0].dolares if d0.moneda == 'D' else p.lineas[0].soles
        for rt in it.retenciones:
            en_doc = round(rt.soles / p.tc, 2) if d0.moneda == 'D' else rt.soles
            disp = idx.disponible(d0)
            if pago0 + en_doc > disp + cfg.TOL_SALDO:
                nota = f'pago + retención ({pago0 + en_doc:,.2f}) supera el saldo de {clave_documento(d0.numero)} ({disp:,.2f})'
                p.estado = 'revisar'
                p.motivos.append('Retención: ' + nota)
            elif abs(pago0 + en_doc - disp) <= cfg.TOL_SALDO:
                nota = 'pago + retención cancelan la factura'
            else:
                nota = f'queda saldo {disp - pago0 - en_doc:,.2f} en {clave_documento(d0.numero)}'
            p.retenciones.append(RetencionInfo(rt.numero, rt.soles, en_doc, nota))
    if p.estado == 'revisar':
        return
    for l in p.lineas:
        idx.usar(l.doc, l.dolares if l.doc.moneda == 'D' else l.soles)
    # ---- comisión
    if com:
        ruc_b, nom_b = bancos.get(m.banco, bancos.get('BCP'))
        op = re.sub(r'\D', '', m.operacion)
        op = op if op.strip('0') else m.fecha.strftime('%d%m%Y')
        q, rr = _q_r(com, p)
        p.lineas.append(Linea(tipo='13', numero=f'0000-{op}', fecha_doc=m.fecha, fecha_venc=m.fecha, ruc=ruc_b, nombre=nom_b,
                              monto=com, soles=q, dolares=rr, cuenta=cfg.CUENTA_COMISION, es_comision=True))


def _interp_de(it: Interpretacion, nombre: str, lineas: list, comision: float, moneda_cuenta: str) -> Interpretacion:
    """Interpretación armada con lo que decidió el equipo: [[n° documento, monto en la moneda de la cuenta], …]."""
    from motor.sustento import Referencia
    mon = 'USD' if moneda_cuenta == 'D' else 'PEN'
    return Interpretacion('pago', proveedor_texto=nombre, comision=round(comision or 0.0, 2), retenciones=list(it.retenciones),
                          centro_costo=it.centro_costo, referencias=[Referencia(str(n), '', round(float(x), 2), mon) for n, x in lineas])


def _reiniciar(p: Propuesta):
    p.estado = 'listo'
    p.motivos, p.lineas, p.retenciones, p.notas = [], [], [], []


def _lineas_por_patron(pat: dict, p: Propuesta, it: Interpretacion, idx: Indice4212) -> list:
    """Documentos del proveedor del patrón: el de saldo igual al pago, o los más antiguos hasta cubrirlo."""
    neto = round(p.importe - it.comision, 2)
    docs = sorted([d for d in idx.por_ruc.get(pat['ruc'], []) if idx.disponible(d) > 0.05],
                  key=lambda d: (d.fecha_doc or date.min, d.numero))
    en_cuenta = lambda d: _de_moneda_doc(idx.disponible(d), p, d)
    if pat['modo'] == 'monto':
        iguales = [d for d in docs if abs(en_cuenta(d) - neto) <= cfg.TOL_SALDO]
        return [[iguales[0].numero, neto]] if len(iguales) == 1 else []
    out, resto = [], neto
    for d in docs:
        if resto <= 0.009:
            break
        x = round(min(en_cuenta(d), resto), 2)
        out.append([d.numero, x])
        resto = round(resto - x, 2)
    return out if resto <= cfg.TOL_CUADRE else []


def _q_r(monto: float, p: Propuesta) -> tuple[float, float]:
    """(soles, dólares) de un monto en la moneda de la cuenta bancaria."""
    if p.moneda == 'D':
        return round(monto * p.tc, 2), round(monto, 2)
    return round(monto, 2), round(monto / p.tc, 2)


def _en_moneda_doc(monto_cuenta: float, p: Propuesta, d: Documento) -> float:
    q, r = _q_r(monto_cuenta, p)
    return r if d.moneda == 'D' else q


def _de_moneda_doc(monto_doc: float, p: Propuesta, d: Documento) -> float:
    if (d.moneda == 'D') == (p.moneda == 'D'):
        return monto_doc
    return round(monto_doc * p.tc, 2) if d.moneda == 'D' else round(monto_doc / p.tc, 2)
