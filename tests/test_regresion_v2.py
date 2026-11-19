# -*- coding: utf-8 -*-
"""Reproduce el archivo PRUEBA_PAGOS_RALUMIN_V2 que NewContaSis importó bien el 18/11/2026 (3 asientos cuadrados).
Necesita los archivos reales de Ralumin (no van en el repositorio): RALUMIN_DATOS=/carpeta con 4212_oct.xlsx,
tc_oct.pdf y v2.xlsx. Sin esa carpeta la prueba se salta."""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATOS = Path(os.environ.get('RALUMIN_DATOS', '/nonexistent'))
pytestmark = pytest.mark.skipif(not (DATOS / '4212_oct.xlsx').exists(), reason='sin datos reales de Ralumin')


def test_reproduce_v2():
    import openpyxl
    from motor.banco import Movimiento
    from motor.cruce import proponer
    from motor.cxp import leer_pendientes
    from motor.exportar import asientos_de_pagos, excel_importacion
    from motor.tipo_cambio import HistorialTC, leer_pdf_sunat
    import io

    docs, _ = leer_pendientes((DATOS / '4212_oct.xlsx').read_bytes())
    h = HistorialTC(leer_pdf_sunat((DATOS / 'tc_oct.pdf').read_bytes()))
    f = date(2026, 10, 31)
    base = dict(pestana='BCP-OCT-26', banco='BCP', col_asiento=6, fecha=f, moneda_texto='USD', asiento='',
                nro_cuenta='', letra='C')
    movs = [
        Movimiento(fila=6, detalle='A 193 1234567 1', operacion='900001', monto=-128.62,
                   sustento='PAGO SCHARFF INTERNATIONAL COURIER & CARGO SA FAC. F028-447802', **base),
        Movimiento(fila=7, detalle='TRANSF.EXT N900002', operacion='900002', monto=-5269.00,
                   sustento='PAGO RG DRILLS ACEROS DE PERFORACION LIMITADA INVOICE 21 $ 2,500,00; INVOICE 22 $ 2,700,00; '
                            'COMISION DEL BANCO $ 69,00', **base),
        Movimiento(fila=8, detalle='TRANFERENCIA CCE', operacion='900003', monto=-465.90,
                   sustento='PAGO GESTION TECNICA MINERA E INDUSTRIAL PERU SAC FAC. E001-5835 $ 464,37; COMISION DEL BANCO $ 1,53; '
                            'COM. RETENCION E001-9999 S/. 49,79', **base),
    ]
    ps = proponer(movs, docs, h)
    assert [p.estado for p in ps] == ['listo'] * 3, [(p.estado, p.motivos) for p in ps]
    gest = ps[2]
    assert gest.retenciones and 'cancelan la factura' in gest.retenciones[0].nota
    asientos, _ = asientos_de_pagos(ps)
    assert all(not a.errores() for a in asientos)
    nuevo = openpyxl.load_workbook(io.BytesIO(excel_importacion(asientos))).active
    v2 = openpyxl.load_workbook(DATOS / 'v2.xlsx').active
    filas_n = sorted([list(r) for r in nuevo.iter_rows(values_only=True)], key=lambda r: (r[1], r[2], r[10]))
    filas_v = sorted([list(r) for r in v2.iter_rows(values_only=True)], key=lambda r: (r[1], r[2], r[10]))
    assert len(filas_n) == len(filas_v) == 6
    for a, b in zip(filas_n, filas_v):
        for col in range(20):              # A..T: todo menos glosa y centros de costo
            assert a[col] == b[col], (col, a, b)
