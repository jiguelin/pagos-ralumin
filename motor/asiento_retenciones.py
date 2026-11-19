# -*- coding: utf-8 -*-
"""
motor/asiento_retenciones.py — Retenciones del mes → plantilla de pagos (4212 al Debe, 401141 al Haber, sin 104).

Entrada: el Excel de SUNAT "Consulta de Comprobantes Emitidos" (comprobantes de retención que emite Ralumin) y la 4212.
Un asiento por comprobante de retención: B = 20, C = serie-número del comprobante (E001-1047), G = 401141.
Cada factura del comprobante es una línea: K = tipo, L = serie-número como en la 4212, S = 4212.

Importes: la retención viene en soles. Si la factura es en dólares, la línea va en dólares (H = D):
R = soles ÷ TC (TC venta SUNAT de la fecha del comprobante = fecha de pago) y Q = soles.
Se valida que el TC que usó el comprobante ((neto + retención) ÷ importe pagado) sea el de SUNAT.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from motor.cruce import parecido, tipo_doc_identidad
from motor.cxp import Documento, clave_documento
from motor.exportar import Asiento
from ralumin import config as cfg


@dataclass
class PropuestaRetencion:
    numero: str
    fecha: object
    ruc: str
    proveedor: str
    total_soles: float
    estado: str = 'listo'           # listo / revisar / sin saldo / anulado / registrado / en lote
    motivos: list[str] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)
    tc: float | None = None
    moneda: str = ''
    lineas: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f'RET:{self.ruc}:{self.numero}'

    @property
    def facturas(self) -> str:
        return ', '.join(clave_documento(l['numero']) for l in self.lineas)


def proponer_retenciones(comprobantes, docs: list[Documento], historial_tc, ya: set[str] | None = None,
                         en_lote: set[str] | None = None, aplicado: dict | None = None) -> list[PropuestaRetencion]:
    ya, en_lote = ya or set(), en_lote or set()
    usado = dict(aplicado or {})
    por_ruc: dict[str, list[Documento]] = {}
    for d in docs:
        por_ruc.setdefault(d.ruc, []).append(d)
    out = []
    for cr in sorted(comprobantes, key=lambda c: (c.fecha, c.serie_numero)):
        from datetime import date
        f = date.fromisoformat(cr.fecha) if cr.fecha else None
        p = PropuestaRetencion(numero=cr.serie_numero, fecha=f, ruc=cr.ruc_proveedor, proveedor=cr.nombre_proveedor,
                               total_soles=cr.total_retenido)
        if cr.anulado:
            p.estado = 'anulado'
            p.motivos.append(f'Comprobante {cr.estado.lower()}: no se registra')
            out.append(p)
            continue
        if p.id in ya:
            p.estado = 'registrado'
            out.append(p)
            continue
        if p.id in en_lote:
            p.estado = 'en lote'
            out.append(p)
            continue
        t, exacto = historial_tc.buscar(f) if (historial_tc is not None and f) else (None, False)
        if not t:
            p.estado = 'revisar'
            p.motivos.append(f'Falta el TC SUNAT del {f:%d/%m/%Y}' if f else 'Sin fecha')
            out.append(p)
            continue
        p.tc = t.venta
        monedas = set()
        for r in cr.referencias:
            cands = [d for d in por_ruc.get(cr.ruc_proveedor, []) if d.clave_doc == clave_documento(r.serie_numero)]
            if not cands:
                p.estado = 'sin saldo' if p.estado in ('listo', 'sin saldo') else p.estado
                p.motivos.append(f'{r.serie_numero}: no está pendiente en la 4212 de {cr.nombre_proveedor[:30]} '
                                 f'(¿retención ya registrada o falta el pago?)')
                continue
            d = cands[0]
            monedas.add(d.moneda)
            if d.moneda == 'D':
                dol = round(r.retencion / t.venta, 2)
                sol = round(r.retencion, 2)
                if r.tc_pago and abs(r.tc_pago - t.venta) > cfg.TOL_TC:
                    p.estado = 'revisar'
                    p.motivos.append(f'{r.serie_numero}: el comprobante usó TC {r.tc_pago:.3f} y SUNAT venta del '
                                     f'{t.fecha:%d/%m} es {t.venta:.3f}')
                esperado = round(0.03 * (r.pagado or r.total), 2)
                if abs(dol - esperado) > 0.02:
                    p.notas.append(f'{r.serie_numero}: retención US$ {dol:,.2f}; el 3 % del pago es US$ {esperado:,.2f}')
                monto_doc = dol
            else:
                sol = round(r.retencion, 2)
                dol = round(sol / t.venta, 2)
                monto_doc = sol
            k = (d.ruc, d.tipo, d.numero)
            disp = round(d.deuda - usado.get(k, 0.0), 2)
            if monto_doc > disp + cfg.TOL_SALDO:
                p.estado = 'revisar'
                p.motivos.append(f'{clave_documento(d.numero)}: la retención ({monto_doc:,.2f}) supera el saldo pendiente ({disp:,.2f})')
            elif abs(monto_doc - disp) <= cfg.TOL_SALDO:
                p.notas.append(f'{clave_documento(d.numero)}: con la retención queda cancelada')
            else:
                p.notas.append(f'{clave_documento(d.numero)}: después de la retención queda saldo {disp - monto_doc:,.2f} '
                               f'(¿falta registrar el pago?)')
            usado[k] = round(usado.get(k, 0.0) + monto_doc, 2)
            p.lineas.append(dict(tipo=d.tipo, numero=d.numero, fecha_doc=d.fecha_doc, fecha_venc=d.fecha_venc or d.fecha_doc,
                                 ruc=d.ruc, nombre=d.proveedor, monto_doc=monto_doc, soles=sol, dolares=dol,
                                 cuenta=d.cuenta or cfg.CUENTA_PROVEEDORES, moneda_doc=d.moneda))
        if len(monedas) > 1:
            p.estado = 'revisar'
            p.motivos.append('El comprobante tiene facturas en soles y en dólares: revisar a mano')
        p.moneda = 'D' if monedas == {'D'} else 'S'
        if p.estado == 'sin saldo' and p.lineas:
            p.estado = 'revisar'          # parte del comprobante sí está pendiente y parte no
        if p.estado == 'listo' and not p.lineas:
            p.estado = 'revisar'
        out.append(p)
    return out


def asientos_de_retenciones(propuestas: list[PropuestaRetencion]) -> list[Asiento]:
    out = []
    for p in propuestas:
        a = Asiento(ref=p.id, fecha=p.fecha, b='20', c=p.numero, tipo_prov=tipo_doc_identidad(p.ruc), ruc=p.ruc,
                    nombre=p.proveedor, cuenta_pago=cfg.CUENTA_RETENCIONES, moneda=p.moneda, tc=p.tc, medio='',
                    glosa=f'RETENCION {p.numero} {p.facturas}'[:60])
        for l in p.lineas:
            monto = l['dolares'] if p.moneda == 'D' else l['soles']
            a.lineas.append(dict(tipo=l['tipo'], numero=l['numero'], fecha_doc=l['fecha_doc'], fecha_venc=l['fecha_venc'],
                                 ruc=l['ruc'], nombre=l['nombre'], monto=monto, soles=l['soles'], dolares=l['dolares'],
                                 cuenta=l['cuenta'], moneda_doc=l['moneda_doc']))
        out.append(a)
    return out
