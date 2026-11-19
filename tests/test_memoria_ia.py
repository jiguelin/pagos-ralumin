# -*- coding: utf-8 -*-
"""Memoria (lo que la app aprende) e IA (con la respuesta simulada)."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor import ia as IA
from motor.almacen import Almacen
from motor.banco import Movimiento
from motor.cruce import Indice4212, proponer
from motor.cxp import Documento
from motor.memoria import Memoria, patron_de
from motor.tipo_cambio import TC, HistorialTC
from motor import lotes as L

H = HistorialTC([TC(date(2026, 10, d), 3.437, 3.442) for d in range(1, 32)] + [TC(date(2026, 11, d), 3.4, 3.41) for d in range(1, 31)])


def _doc(ruc, nombre, numero, deuda, fecha=date(2026, 9, 1), tipo='01'):
    return Documento(ruc=ruc, proveedor=nombre, tipo=tipo, numero=numero, fecha_doc=fecha, fecha_venc=None, moneda='D',
                     saldo_usd=-deuda, saldo_pen=0.0)


def _mov(fila, op, monto, sustento, f=date(2026, 10, 5)):
    return Movimiento(pestana='BCP-OCT-26', banco='BCP', fila=fila, col_asiento=6, fecha=f, detalle='A 191 1',
                      operacion=op, monto=monto, moneda_texto='USD', asiento='', nro_cuenta='', letra='C', sustento=sustento)


def test_patron_ignora_numeros_y_meses():
    assert patron_de('PAGO DE MARCA GIAN CALDERON (SETIEMBRE)') == patron_de('PAGO DE MARCA GIAN CALDERON (OCTUBRE) S/ 950,00')


def test_solucion_alias_y_patron(tmp_path):
    alm = Almacen(ruta_sqlite=str(tmp_path / 'm.db'))
    mem = Memoria(alm)
    docs = [_doc('45019020', 'CALDERON RODAS GIAN FRANCE', '000013-0000000009360', 950.0),
            _doc('45019020', 'CALDERON RODAS GIAN FRANCE', '000013-0000000009999', 950.0, fecha=date(2026, 10, 1))]
    m1 = _mov(6, '219272', -950.0, 'PAGO DE MARCA GIAN CALDERON (SETIEMBRE)')
    p = proponer([m1], docs, H, memoria=mem)[0]
    assert p.estado == 'revisar'
    mem.aprender(mov_id=m1.id, firma=L.firma_mov(m1), sustento=m1.sustento, proveedor_texto='MARCA GIAN CALDERON',
                 ruc='45019020', nombre='CALDERON RODAS GIAN FRANCE', lineas=[['000013-0000000009360', 950.0]], comision=0,
                 origen='equipo', modo_patron='antiguo')
    mem = Memoria(alm)                                   # lo aprendido sobrevive (está en la base)
    p = proponer([m1], docs, H, memoria=mem)[0]
    assert p.estado == 'listo' and p.lineas[0].numero == '000013-0000000009360'
    # el mes siguiente: mismo texto, otro mes → patrón (documento más antiguo con saldo)
    m2 = _mov(7, '300001', -950.0, 'PAGO DE MARCA GIAN CALDERON (OCTUBRE)', f=date(2026, 11, 3))
    p2 = proponer([m2], docs, H, memoria=mem)[0]
    assert p2.estado == 'listo' and 'Patrón' in p2.notas[0]


def test_ia_descarta_documentos_inventados(monkeypatch):
    docs = [_doc('20550112737', 'GESTION TECNICA MINERA E INDUSTRIAL PERU SAC', '00E001-0000000005835', 478.73)]
    p = proponer([_mov(6, '1', -465.90, 'PAGO GESTEMIND COMISION DEL BANCO $ 1,53')], docs, H)[0]
    idx = Indice4212(docs)
    resp = {'ruc': '20550112737', 'documentos': [{'numero': '00E001-0000000005835', 'monto': 464.37},
                                                 {'numero': '00E001-9999', 'monto': 10}], 'comision': 1.53, 'confianza': 90, 'razon': 'x'}
    monkeypatch.setattr(IA, '_llamar', lambda *a, **k: json.dumps(resp))
    s = IA.sugerir(p, idx, 'clave', 'modelo')
    assert s.lineas == [['00E001-0000000005835', 464.37]] and any('9999' in a for a in s.avisos)
    resp['ruc'] = '20999999999'
    s = IA.sugerir(p, idx, 'clave', 'modelo')
    assert s.ruc == '' and s.lineas == []
