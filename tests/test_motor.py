# -*- coding: utf-8 -*-
"""Pruebas sin datos reales: lectura de montos y sustentos, reglas del Excel y lotes."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.banco import Movimiento, leer_fecha, leer_monto
from motor.cruce import proponer
from motor.cxp import Documento, clave_documento
from motor.exportar import asientos_de_pagos, ordenar
from motor.sustento import interpretar, numero_texto
from motor.tipo_cambio import TC, HistorialTC
from motor import lotes as L


def test_montos_del_sheet():
    assert leer_monto('-$1.019,00') == (-1019.0, 'USD')
    assert leer_monto('$59.659,65') == (59659.65, 'USD')
    assert leer_monto('-S/ 1,000.00') == (-1000.0, 'PEN')
    assert leer_monto('S/ -1.140,00') == (-1140.0, 'PEN')
    assert leer_monto('S/ 99.00') == (99.0, 'PEN')


def test_fechas():
    assert leer_fecha('1 de septiembre de 2026') == date(2026, 9, 1)
    assert leer_fecha('02 de septiembre de 2026') == date(2026, 9, 2)
    assert leer_fecha('31/10/2026') == date(2026, 10, 31)


def test_numeros_del_sustento():
    assert numero_texto('4,320,87') == 4320.87
    assert numero_texto('1177,70') == 1177.70
    assert numero_texto('7,60,94') == 760.94
    assert numero_texto('2,017,6') == 2017.6


def test_sustento_con_comision_y_retencion():
    it = interpretar('PAGO VILLAFE PARTS EIRL FAC. E001-29 $ 4,320,87; COMISION DEL BANCO $ 1,53; COM. RETENCION E001-1030 S/. 450,23')
    assert it.tipo == 'pago' and it.proveedor_texto == 'VILLAFE PARTS EIRL'
    assert [(r.texto, r.monto) for r in it.referencias] == [('E001-29', 4320.87)]
    assert it.comision == 1.53
    assert [(r.numero, r.soles) for r in it.retenciones] == [('E001-1030', 450.23)]


def test_fuera_de_alcance():
    assert interpretar('BCP IMPUESTO ITF').tipo == 'fuera'
    assert interpretar('ABONO A LA CUENTA DE CAJA CHICA').tipo == 'fuera'


def test_egresos_sin_sustento_no_se_esconden():
    assert interpretar('', 'P.P.: 00110814009606181682-11').tipo == 'sin sustento'
    assert interpretar('', 'COMISION DE MANTENIMIENTO').tipo == 'fuera'
    assert interpretar('4TO. ABONO SERNA DIAZ MARIA VICTORIA').tipo == 'sin sustento'


def test_clave_documento():
    assert clave_documento('00E001-0000000000029') == clave_documento('E001-29') == 'E001-29'


def _doc(ruc, nombre, numero, deuda, tipo='01', moneda='D'):
    return Documento(ruc=ruc, proveedor=nombre, tipo=tipo, numero=numero, fecha_doc=date(2026, 9, 1), fecha_venc=None,
                     moneda=moneda, saldo_usd=-deuda, saldo_pen=0.0)


def _mov(fila, op, monto, sustento):
    return Movimiento(pestana='BCP-OCT-26', banco='BCP', fila=fila, col_asiento=6, fecha=date(2026, 10, 5), detalle='TRANFERENCIA CCE',
                      operacion=op, monto=monto, moneda_texto='USD', asiento='', nro_cuenta='', letra='C', sustento=sustento)


H = HistorialTC([TC(date(2026, 10, 5), 3.437, 3.442)])


def test_misma_serie_otro_proveedor_no_se_cruza():
    docs = [_doc('20615221849', 'JMV PARTS E.I.R.L.', '00E001-0000000000029', 5000)]
    ps = proponer([_mov(6, '111', -100.0, 'PAGO VILLAFE PARTS EIRL FAC. E001-29')], docs, H)
    assert ps[0].estado == 'revisar'


def test_pago_en_varias_operaciones_y_regla_bc():
    docs = [_doc('20557489852', 'HP SUPPLY PERU S.A.C.', '00E001-0000000000025', 15542.45)]
    movs = [_mov(6, '015129', -5001.53, 'PAGO HP SUPPLY PERU SAC FAC. E001-25 $ 5,000,00; COMISION DEL BANCO $ 1,53'),
            _mov(7, '014150', -10001.53, 'PAGO HP SUPPLY PERU SAC FAC. E001-25 $ 10,000,00; COMISION DEL BANCO $ 1,53'),
            _mov(8, '002665', -542.45, 'PAGO HP SUPPLY PERU SAC FAC. E001-25 $ 540,92; COMISION DEL BANCO $ 1,53')]
    ps = proponer(movs, docs, H)
    assert [p.estado for p in ps] == ['listo'] * 3
    asientos, usados = asientos_de_pagos(ps)
    assert [(a.b, a.c) for a in asientos] == [('01', '00E001-0000000000025'), ('13', '014150'), ('13', '002665')]
    for a in asientos:
        assert not a.errores()
        assert round(sum(l['monto'] for l in a.lineas), 2) == round(-[p for p in ps if p.mov.id == a.ref][0].mov.monto, 2)
    assert ordenar(asientos)[0].b == '01'
    # en un lote posterior, la misma factura ya usada también va con 13 + operación
    p_extra = ps[1]
    asientos2, _ = asientos_de_pagos([p_extra], usados)
    assert asientos2[0].b == '13'


def test_pago_que_supera_el_saldo():
    docs = [_doc('20110964928', 'SCHARFF INTERNATIONAL COURIER & CARGO SA', '00F028-0000000447802', 100.0)]
    ps = proponer([_mov(6, '1', -128.62, 'PAGO SCHARFF INTERNATIONAL COURIER & CARGO SA FAC. F028-447802')], docs, H)
    assert ps[0].estado == 'revisar'


def test_lotes_bloquean_y_descuentan(tmp_path):
    from motor.almacen import Almacen
    alm = Almacen(ruta_sqlite=str(tmp_path / 'p.db'))
    docs = [_doc('20110964928', 'SCHARFF INTERNATIONAL COURIER & CARGO SA', '00F028-0000000447802', 128.62)]
    movs = [_mov(6, '900001', -128.62, 'PAGO SCHARFF INTERNATIONAL COURIER & CARGO SA FAC. F028-447802')]
    ps = proponer(movs, docs, H)
    asientos, _ = asientos_de_pagos(ps)
    info = {p.mov.id: dict(pestana=p.mov.pestana, fila=p.mov.fila, col_asiento=6, firma=L.firma_mov(p.mov)) for p in ps}
    L.crear(alm, 'pagos', asientos, info, 'prueba', '04')
    est = L.estado(L.listar(alm), 'pagos', '2026-01-01')
    assert proponer(movs, docs, H, aplicado=est['aplicado'], en_lote=est['en_lote'])[0].estado == 'en lote'
    L.confirmar(alm, 1, 2, '04')
    est = L.estado(L.listar(alm), 'pagos', '2026-01-01')
    assert L.listar(alm)[0]['items'][0]['asiento'] == '04-2'
    assert proponer(movs, docs, H, registrados_app=est['registrados'])[0].estado == 'registrado'


def test_sin_sustento_busca_por_monto():
    from datetime import date
    from motor.banco import Movimiento
    from motor.cruce import proponer
    from motor.cxp import Documento
    from motor.tipo_cambio import HistorialTC, TC
    h = HistorialTC([TC(date(2026, 9, 10), 3.50, 3.52)])
    docs = [Documento('20111111111', 'ACME SAC', '01', 'F001-00000010', date(2026, 8, 1), None, 'S', 0, -1000.0, '4212'),
            Documento('20111111111', 'ACME SAC', '01', 'F001-00000011', date(2026, 8, 5), None, 'S', 0, -500.0, '4212'),
            Documento('20222222222', 'OTRA SAC', '01', 'F002-00000001', date(2026, 8, 5), None, 'S', 0, -777.0, '4212')]
    m = Movimiento(pestana='BBVA-SEPT-26', banco='BBVA', fila=9, col_asiento=6, fecha=date(2026, 9, 10),
                   detalle='P.P.: 0011', operacion='1', monto=-1500.0, moneda_texto='S/', asiento='',
                   nro_cuenta='011-616-000200310321-01', letra='B', sustento='')
    p = proponer([m], docs, h)[0]
    assert p.estado == 'revisar' and p.tc
    assert p.coincidencias and p.coincidencias[0].ruc == '20111111111' and len(p.coincidencias[0].lineas) == 2
    m.monto = -970.0                                     # 1,000 menos 3 % de retención
    p = proponer([m], docs, h)[0]
    assert any(c.forma == 'con retención 3%' and c.lineas == [('F001-00000010', 970.0)] for c in p.coincidencias)
