# -*- coding: utf-8 -*-
"""
motor/memoria.py — Lo que la app aprende del equipo (se guarda en el almacén: SQLite local o Neon).

Tres cosas:
  · alias       texto del proveedor en el Sheet → RUC   ("IIPS GMBH" → IPS GMBH). Se usa antes de comparar nombres.
  · soluciones  un pago concreto del banco → proveedor, documentos y montos que eligió el equipo (o la IA, aceptada).
  · patrones    un texto que se repite cada mes ("PAGO DE MARCA GIAN CALDERON (SETIEMBRE)") → proveedor y cómo elegir
                el documento (el de saldo igual al pago, o los más antiguos primero). Los números y meses no cuentan.

Todo queda a la vista en Configuración y se puede borrar.
"""
from __future__ import annotations

import re
from datetime import datetime

from motor.banco import MESES
from motor.cruce import nombre_base

CLAVE = 'memoria'
_MESES_TXT = r'\b(' + '|'.join(sorted(MESES, key=len, reverse=True)) + r'|SETIEMBRE|SET|SEPT?|ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|OCT|NOV|DIC)\b'


def patron_de(sustento: str) -> str:
    """Texto del sustento sin números, montos, monedas ni meses: lo que se repite de un mes a otro."""
    import unicodedata
    s = unicodedata.normalize('NFKD', str(sustento or '')).encode('ascii', 'ignore').decode().upper()
    s = re.sub(r'S/\.?|\$|US\$', ' ', s)
    s = re.sub(r'[A-Z]{0,4}\d[\d.,/\-]*', ' ', s)          # números, series, montos, fechas
    s = re.sub(_MESES_TXT, ' ', s)
    s = re.sub(r'[^A-Z ]', ' ', s)
    return ' '.join(w for w in s.split() if len(w) > 1)


class Memoria:
    def __init__(self, alm):
        self.alm = alm
        d = alm.leer(CLAVE, defecto=None) or {}
        self.alias: dict[str, dict] = d.get('alias', {})
        self.soluciones: dict[str, dict] = d.get('soluciones', {})
        self.patrones: dict[str, dict] = d.get('patrones', {})

    # ------------------------------------------------------------------ consultas
    def ruc_alias(self, texto: str) -> str:
        r = self.alias.get(nombre_base(texto))
        return r['ruc'] if r else ''

    def solucion(self, mov_id: str, firma: str) -> dict | None:
        return self.soluciones.get(mov_id) or self.soluciones.get(firma)

    def patron(self, sustento: str) -> dict | None:
        p = patron_de(sustento)
        return self.patrones.get(p) if len(p) >= 8 else None

    # ------------------------------------------------------------------ escritura
    def _guardar(self):
        self.alm.guardar(CLAVE, dict(alias=self.alias, soluciones=self.soluciones, patrones=self.patrones))

    def aprender(self, *, mov_id: str, firma: str, sustento: str, proveedor_texto: str, ruc: str, nombre: str,
                 lineas: list[list], comision: float, origen: str, recordar_alias: bool = True,
                 modo_patron: str = '') -> None:
        """lineas: [[numero_documento, monto_en_moneda_de_la_cuenta], …]. modo_patron: '' / 'monto' / 'antiguo'."""
        ahora = datetime.now().isoformat(timespec='seconds')
        self.soluciones[mov_id] = dict(firma=firma, ruc=ruc, nombre=nombre, lineas=lineas, comision=round(comision or 0, 2),
                                       sustento=sustento[:200], origen=origen, creado=ahora)
        if firma:
            self.soluciones[firma] = self.soluciones[mov_id]
        if recordar_alias and proveedor_texto and nombre_base(proveedor_texto):
            self.alias[nombre_base(proveedor_texto)] = dict(ruc=ruc, nombre=nombre, texto=proveedor_texto[:80], origen=origen, creado=ahora)
        if modo_patron:
            p = patron_de(sustento)
            if len(p) >= 8:
                self.patrones[p] = dict(ruc=ruc, nombre=nombre, modo=modo_patron, ejemplo=sustento[:120], origen=origen, creado=ahora)
        self._guardar()

    def olvidar(self, tipo: str, claves: list[str]):
        dest = {'alias': self.alias, 'patrones': self.patrones, 'soluciones': self.soluciones}[tipo]
        for k in claves:
            sol = dest.pop(k, None)
            if tipo == 'soluciones' and sol and sol.get('firma'):
                dest.pop(sol['firma'], None)
        self._guardar()
