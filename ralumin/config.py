# -*- coding: utf-8 -*-
"""
ralumin/config.py — Reglas de RALUMIN E.I.R.L. (RUC 20602768440) para la plantilla de pagos de NewContaSis.
Todo lo validado con el usuario y con las pruebas de importación del 18/11/2026.
"""

RUC_EMPRESA = '20602768440'
SHEET_ID = ''        # va en los Secrets (SHEET_ID): el Sheet está abierto a quien tenga el enlace

# Cuenta bancaria del Sheet → (cuenta contable, moneda 'D'/'S', banco). Se busca primero por número de cuenta y,
# si la pestaña no lo trae (BCP-OCT-26 solo tiene la letra), por (banco, letra).
CUENTAS_POR_NUMERO = {
    '191-2480838-1-77': ('104102', 'D', 'BCP'),
    '191-2617407-0-53': ('104103', 'S', 'BCP'),
    '193-70495357-0-29': ('104101', 'S', 'BCP'),           # caja chica
    '004-7455618': ('104104', 'D', 'SCOTIA'),
    '011-616-000200310321-01': ('104105', 'S', 'BBVA'),
}
CUENTAS_POR_LETRA = {
    ('BCP', 'B'): ('104103', 'S'),
    ('BCP', 'C'): ('104102', 'D'),
    ('BCP', 'D'): ('104101', 'S'),
    ('SCOTIA', 'C'): ('104104', 'D'),
    ('BBVA', 'B'): ('104105', 'S'),
}

# Banco como entidad (línea de la comisión)
RUC_BANCO = {
    'BCP': ('20100047218', 'BANCO DE CREDITO DEL PERU'),
    'SCOTIA': ('20100043140', 'SCOTIABANK PERU SAA'),
    'BBVA': ('20100130204', 'BANCO CONTINENTAL'),
}

CUENTA_COMISION = '6391094'      # gastos bancarios, siempre (dólares y soles)
CUENTA_PROVEEDORES = '4212'
CUENTA_RETENCIONES = '401141'

# Importación en NewContaSis
SUBDIARIO = '04'                 # Egresos Caja-Bancos
LIBRO = '01'                     # Libro Caja y Bancos
FLUJO_EFECTIVO = 'F055'          # (-) Pago a proveedores de bienes y servicios

# Medio de pago (columna T)
MEDIO_TRANSFERENCIA = '003'
MEDIO_EXTERIOR = '101'

# Centro de costo (columna V) cuando el sustento lo menciona
CENTROS_COSTO = {'CALLE 23': '0101'}

# Tolerancias
TOL_CUADRE = 0.01                # cuadre del pago contra el banco
TOL_SALDO = 0.05                 # pago mayor que el saldo de la factura
TOL_TC = 0.0015                  # TC de la retención contra SUNAT venta

# Textos del sustento que NO son pagos a proveedores de la 4212 (fuera de esta plantilla)
FUERA_DE_ALCANCE = [
    ('IMPUESTO ITF', 'ITF'), (' ITF', 'ITF'), ('BCP ', 'Gasto bancario'), ('COM.MANTENIM', 'Gasto bancario'),
    ('MANTENIMIENTO', 'Gasto bancario'), ('ENVIO', 'Gasto bancario'), ('LETRAS DESCUENTOS', 'Gasto bancario'),
    ('COMISION TRANSF', 'Gasto bancario'), ('COMIS ', 'Gasto bancario'), ('PAGOS AFP', 'Planilla / AFP'), ('AFP ', 'Planilla / AFP'),
    ('SUNAT', 'Impuestos'), ('SUELDOS', 'Planilla'), ('HABER TLC', 'Planilla'), ('CAJA CHICA', 'Caja chica'),
    ('ENTRE CUENTAS', 'Transferencia entre cuentas'), ('CAMBIO', 'Cambio de moneda'), ('RETIRO', 'Retiro'),
    ('POR ERROR', 'Error / devolución'), ('DEVOLUCI', 'Error / devolución'), ('ANULADA', 'Error / devolución'),
    ('PAPELETA', 'Papeletas / multas'), ('TERRENO', 'Compra de terreno'),
]
