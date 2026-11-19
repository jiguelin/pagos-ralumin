# -*- coding: utf-8 -*-
"""
motor/ia.py — Claude (Anthropic) SOLO para pagos dudosos, como sugerencia.

Reglas:
  · La IA elige exclusivamente entre los proveedores y documentos PENDIENTES que le pasa el sistema (la 4212 cargada).
    Si responde un RUC o un documento que no está en la lista, la sugerencia se descarta.
  · Se valida que facturas + comisión sumen el cargo del banco.
  · Nunca entra sola a un lote: el equipo la acepta o la corrige, y lo aceptado se guarda en la memoria (la próxima vez
    ese pago se resuelve sin consultar a la IA).

Clave: ANTHROPIC_API_KEY en los Secrets. Modelo: IA_MODELO (por defecto claude-haiku-5-5).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field

from motor.cruce import Indice4212, Propuesta, _de_moneda_doc, parecido
from motor.cxp import clave_documento

MODELO_DEFECTO = 'claude-haiku-5-5'


@dataclass
class Sugerencia:
    ruc: str
    nombre: str
    lineas: list[list] = field(default_factory=list)     # [[numero, monto en moneda de la cuenta]]
    comision: float = 0.0
    confianza: int = 0
    razon: str = ''
    avisos: list[str] = field(default_factory=list)


def _llamar(prompt: str, api_key: str, modelo: str, max_tokens: int = 700) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=modelo, max_tokens=max_tokens, messages=[{'role': 'user', 'content': prompt}])
        return ''.join(getattr(b, 'text', '') for b in msg.content)
    except ImportError:
        body = json.dumps({'model': modelo, 'max_tokens': max_tokens, 'messages': [{'role': 'user', 'content': prompt}]}).encode()
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body, method='POST',
                                     headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        return ''.join(b.get('text', '') for b in data.get('content', []))


def candidatos(p: Propuesta, idx: Indice4212, max_prov: int = 6, max_docs: int = 25) -> list[dict]:
    """Proveedores más parecidos al texto del sustento (o al RUC ya identificado) con sus documentos pendientes."""
    texto = (p.interp.proveedor_texto if p.interp else '') or p.mov.sustento
    ranking = sorted(idx.nombres.items(), key=lambda kv: -parecido(texto, kv[1]))
    rucs = ([p.ruc] if p.ruc in idx.nombres else []) + [r for r, _ in ranking if r != p.ruc]
    out = []
    for ruc in rucs[:max_prov]:
        docs = sorted([d for d in idx.por_ruc.get(ruc, []) if idx.disponible(d) > 0.05], key=lambda d: (d.fecha_doc is None, d.fecha_doc))
        if not docs:
            continue
        out.append(dict(ruc=ruc, nombre=idx.nombres[ruc], docs=[dict(numero=d.numero, corto=clave_documento(d.numero), tipo=d.tipo,
                        fecha=d.fecha_doc.isoformat() if d.fecha_doc else '', moneda='US$' if d.moneda == 'D' else 'S/',
                        saldo=idx.disponible(d), cuenta=d.cuenta, glosa=d.glosa[:60]) for d in docs[:max_docs]]))
    return out


def sugerir(p: Propuesta, idx: Indice4212, api_key: str, modelo: str = MODELO_DEFECTO) -> Sugerencia | None:
    cands = candidatos(p, idx)
    if not cands:
        return None
    mon = 'US$' if p.moneda == 'D' else 'S/'
    lista = '\n'.join(
        f"RUC {c['ruc']} | {c['nombre']}\n" + '\n'.join(
            f"   - {d['numero']} ({d['corto']}) tipo {d['tipo']} del {d['fecha']} · saldo {d['moneda']} {d['saldo']:,.2f} · cuenta {d['cuenta']} · {d['glosa']}"
            for d in c['docs']) for c in cands)
    prompt = f"""Eres contador de RALUMIN E.I.R.L. (Perú) y concilias pagos bancarios contra documentos pendientes por pagar.

Pago del banco: {p.mov.fecha:%d/%m/%Y}, cuenta {p.cuenta} en {mon}, cargo {mon} {p.importe:,.2f}, operación {p.mov.operacion}.
Detalle del banco: {p.mov.detalle}
Sustento escrito por el equipo: {p.mov.sustento}
Tipo de cambio del día (venta SUNAT): {p.tc}
Observaciones del sistema: {'; '.join(p.motivos)}

Reglas:
- "COMISION DEL BANCO" es gasto bancario: va aparte en "comision", no a una factura.
- "COM. RETENCION ... S/ ..." es informativa: NO es parte del pago, ignórala para los montos.
- Los montos que devuelvas van en {mon} (moneda de la cuenta). Si la factura está en otra moneda, convierte con el tipo de cambio.
- Facturas + comisión deben sumar exactamente {mon} {p.importe:,.2f}.
- Solo puedes elegir UN proveedor y documentos de ESTA lista (están pendientes en la contabilidad). Prohibido inventar.
  Si ninguno corresponde, responde "ruc": "".

Proveedores y documentos pendientes:
{lista}

Responde SOLO un JSON:
{{"ruc": "<RUC de la lista o vacío>", "documentos": [{{"numero": "<número exacto de la lista>", "monto": <número>}}], "comision": <número>, "confianza": <0-100>, "razon": "<una línea, máx 150 caracteres>"}}"""
    raw = _llamar(prompt, api_key, modelo)
    s, e = raw.find('{'), raw.rfind('}') + 1
    if s < 0 or e <= s:
        return None
    data = json.loads(raw[s:e])
    ruc = str(data.get('ruc') or '').strip()
    prov = next((c for c in cands if c['ruc'] == ruc), None)
    if not prov:
        return Sugerencia('', '', razon=str(data.get('razon', ''))[:200], avisos=['La IA no encontró un documento que corresponda.'])
    validos = {d['numero'] for d in prov['docs']}
    sug = Sugerencia(ruc=ruc, nombre=prov['nombre'], comision=round(float(data.get('comision') or 0), 2),
                     confianza=int(data.get('confianza') or 0), razon=str(data.get('razon', ''))[:200])
    for d in data.get('documentos') or []:
        num = str(d.get('numero', '')).strip()
        if num not in validos:
            sug.avisos.append(f'Descartado {num}: no está en la lista de pendientes')
            continue
        sug.lineas.append([num, round(float(d.get('monto') or 0), 2)])
    suma = round(sum(x for _, x in sug.lineas) + sug.comision, 2)
    if abs(suma - p.importe) > 0.01:
        sug.avisos.append(f'Lo sugerido suma {suma:,.2f} y el banco {p.importe:,.2f}: corrija los montos antes de guardar')
    return sug
