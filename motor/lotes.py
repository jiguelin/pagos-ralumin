# -*- coding: utf-8 -*-
"""
motor/lotes.py — Control de lo que ya se mandó a NewContaSis (se guarda en el almacén: SQLite local o Neon).

Ciclo de un lote:
  exportado  → se descargó el Excel; sus pagos quedan bloqueados (no vuelven a proponerse)
  registrado → se importó en NewContaSis y se anotó el "Asiento Inicio": cada pago tiene su 04-N
  anulado    → la importación falló o se descartó: sus pagos vuelven a estar disponibles

Cada pago se reconoce por dos llaves: su id (pestaña + fecha + operación + monto + texto) y su firma (pestaña + fecha +
operación + monto), para que corregir el texto del sustento en el Sheet no lo haga reaparecer como pendiente.
"""
from __future__ import annotations

import json
from datetime import datetime

from motor.almacen import Almacen
from motor.exportar import Asiento, ordenar


def firma_mov(m) -> str:
    return f'{m.pestana.strip()}|{m.fecha.isoformat()}|{m.operacion.strip()}|{-m.monto:.2f}'


def _ahora() -> str:
    return datetime.now().isoformat(timespec='seconds')


def listar(alm: Almacen) -> list[dict]:
    lotes = [alm.leer(k) for k in alm.claves('lote:')]
    return sorted([l for l in lotes if l], key=lambda l: l['num'], reverse=True)


def crear(alm: Almacen, tipo: str, asientos: list[Asiento], info_items: dict[str, dict], descripcion: str,
          subdiario: str) -> dict:
    """`info_items[ref]` trae datos del movimiento (pestaña, fila, columna del asiento, firma…)."""
    n = int(alm.leer('contador_lotes', defecto=0) or 0) + 1
    items = []
    for orden, a in enumerate(ordenar(asientos)):
        extra = info_items.get(a.ref, {})
        items.append(dict(ref=a.ref, orden=orden, b=a.b, c=a.c, fecha=a.fecha.isoformat(), proveedor=a.nombre, ruc=a.ruc,
                          cuenta=a.cuenta_pago, moneda=a.moneda,
                          importe=round(sum(l['monto'] for l in a.lineas), 2),
                          docs=[[l['ruc'], l['tipo'], l['numero'], round(l['dolares'] if l.get('moneda_doc') == 'D' else l['soles'], 2)]
                                for l in a.lineas if not l['cuenta'].startswith('639')],
                          asiento='', **extra))
    lote = dict(num=n, tipo=tipo, creado=_ahora(), descripcion=descripcion, estado='exportado', subdiario=subdiario,
                asiento_inicio=None, registrado=None, items=items, sheet_escrito=False)
    alm.guardar(f'lote:{n:05d}', lote)
    alm.guardar('contador_lotes', n)
    return lote


def confirmar(alm: Almacen, num: int, asiento_inicio: int, subdiario: str) -> dict:
    lote = alm.leer(f'lote:{num:05d}')
    lote['estado'] = 'registrado'
    lote['asiento_inicio'] = int(asiento_inicio)
    lote['subdiario'] = subdiario
    lote['registrado'] = _ahora()
    for it in lote['items']:
        it['asiento'] = f'{subdiario}-{int(asiento_inicio) + it["orden"]}'
    alm.guardar(f'lote:{num:05d}', lote)
    return lote


def anular(alm: Almacen, num: int, motivo: str = '') -> dict:
    lote = alm.leer(f'lote:{num:05d}')
    lote['estado'] = 'anulado'
    lote['anulado'] = _ahora()
    lote['motivo_anulacion'] = motivo
    alm.guardar(f'lote:{num:05d}', lote)
    return lote


def marcar_sheet(alm: Almacen, num: int):
    lote = alm.leer(f'lote:{num:05d}')
    lote['sheet_escrito'] = True
    alm.guardar(f'lote:{num:05d}', lote)


def estado(lotes: list[dict], tipo: str, fecha_4212: str = '') -> dict:
    """Resume los lotes vigentes:
       registrados / en_lote: refs y firmas · usados_bc: B/C ya usados · aplicado: montos por documento que la 4212
       cargada todavía no refleja (lotes exportados, o registrados después de cargar esa 4212)."""
    reg, enl, bc = set(), set(), set()
    aplicado: dict[tuple, float] = {}
    for l in lotes:
        if l['estado'] == 'anulado':
            continue
        for it in l['items']:
            if l['tipo'] == tipo:
                destino = reg if l['estado'] == 'registrado' else enl
                destino.add(it['ref'])
                if it.get('firma'):
                    destino.add(it['firma'])
            bc.add((it['b'], it['c']))
            no_reflejado = l['estado'] == 'exportado' or (l.get('registrado') or '') > (fecha_4212 or '')
            if no_reflejado:
                for ruc, tipo_doc, numero, monto in it.get('docs', []):
                    k = (ruc, tipo_doc, numero)
                    aplicado[k] = round(aplicado.get(k, 0.0) + monto, 2)
    return dict(registrados=reg, en_lote=enl, usados_bc=bc, aplicado=aplicado)
