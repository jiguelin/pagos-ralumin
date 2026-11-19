# -*- coding: utf-8 -*-
"""
Pagos Ralumin — interfaz Streamlit.

    streamlit run app.py

Flujo: archivos (4212 u otra cuenta por pagar, TC SUNAT) → leer el Google Sheet de bancos → propuesta por pago →
elegir un bloque (días, cuenta, proveedor…) → crear lote → descargar el Excel → importar en NewContaSis →
anotar el Asiento Inicio → el app marca los pagos y escribe el 04-N en el Sheet.
Toda la lógica vive en `motor/` y `ralumin/`; este archivo solo dibuja.
"""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import date, datetime

import pandas as pd
import streamlit as st

from motor import __version__
from motor import lotes as L
from motor.almacen import Almacen, ErrorAlmacen
from motor.asiento_retenciones import asientos_de_retenciones, proponer_retenciones
from motor.banco import descargar_sheet, info_pestana, leer_libro, leer_pestana, pestanas_con_bancos
from motor import ia as IA
from motor.cruce import Indice4212, _de_moneda_doc, parecido, proponer
from motor.memoria import Memoria
from motor.cxp import Documento, leer_entidades, leer_pendientes
from motor.exportar import asientos_de_pagos, excel_importacion, excel_revision, ordenar
from motor.retenciones import leer_excel_retenciones
from motor.tipo_cambio import TC, HistorialTC, leer_pdf_sunat
from ralumin import config as cfg

st.set_page_config(page_title='Pagos Ralumin', page_icon='💸', layout='wide')
st.markdown("""
<style>
  .block-container {padding-top: 1.4rem; max-width: 1500px;}
  h1 {font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0;}
  .stMetric {background: #f7f7f9; border-radius: 12px; padding: 8px 14px;}
  .pequeno {font-size: 0.82rem; color: #6b7280;}
</style>
""", unsafe_allow_html=True)


def secreto(clave: str, defecto=''):
    try:
        v = st.secrets.get(clave)
    except Exception:
        v = None
    return v or os.environ.get(clave) or defecto


# ----------------------------------------------------------------------------- clave de acceso
CLAVE = secreto('APP_PASSWORD')
if CLAVE and not st.session_state.get('acceso'):
    st.title('Pagos Ralumin')
    with st.form('acceso_form'):
        intento = st.text_input('Clave de acceso', type='password')
        if st.form_submit_button('Ingresar', type='primary'):
            if intento == CLAVE:
                st.session_state['acceso'] = True
                st.rerun()
            else:
                st.error('Clave incorrecta.')
    st.stop()


@st.cache_resource
def almacen(url: str, version: str) -> Almacen:
    return Almacen(url=url)


try:
    ALM = almacen(secreto('DATABASE_URL'), __version__)
except ErrorAlmacen as ex:
    st.error('No se pudo conectar con la base de datos (DATABASE_URL en los secrets). Detalle: ' + str(ex))
    st.stop()

SHEET_ID = secreto('SHEET_ID', cfg.SHEET_ID)
API_IA = secreto('ANTHROPIC_API_KEY')
MODELO_IA = secreto('IA_MODELO', IA.MODELO_DEFECTO)
MEM = Memoria(ALM)
ESTADOS = {'listo': '🟢 Listo', 'revisar': '🟡 Revisar', 'adelanto': '🔵 Adelanto', 'fuera': '⚪ Fuera de alcance',
           'en lote': '📦 En lote', 'registrado': '✅ Registrado', 'sin saldo': '⚪ Sin saldo', 'anulado': '⚪ Anulado'}


# ----------------------------------------------------------------------------- archivos guardados
def guardar_archivo(clave: str, nombre: str, data: bytes, info: dict | None = None):
    ALM.guardar(clave, dict(nombre=nombre, fecha=datetime.now().isoformat(timespec='seconds'),
                            b64=base64.b64encode(data).decode(), info=info or {}))


def leer_archivo(clave: str) -> tuple[bytes | None, dict]:
    d = ALM.leer(clave)
    if not d:
        return None, {}
    return base64.b64decode(d['b64']), d


def _h(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


@st.cache_data(show_spinner=False)
def _leer_pendientes_cache(h: str, data: bytes):
    return leer_pendientes(data)


@st.cache_data(show_spinner=False)
def _leer_libro_cache(h: str, data: bytes):
    return leer_libro(data)


@st.cache_data(show_spinner=False)
def _leer_ret_cache(h: str, data: bytes, nombre: str):
    return leer_excel_retenciones(data, nombre)


def documentos_pendientes() -> tuple[list[Documento], list[dict], str]:
    """Todos los archivos de cuentas por pagar cargados (última versión de cada cuenta)."""
    docs, resumen, ultima = [], [], ''
    for k in ALM.claves('archivo:pendientes:'):
        data, meta = leer_archivo(k)
        if not data:
            continue
        d, info = _leer_pendientes_cache(_h(data), data)
        docs.extend(d)
        resumen.append(dict(cuenta=info.get('cuenta') or k.split(':')[-1], mes=info.get('mes', ''), documentos=len(d),
                            archivo=meta['nombre'], cargado=meta['fecha']))
        ultima = max(ultima, meta['fecha'])
    return docs, resumen, ultima


def historial_tc() -> HistorialTC:
    filas = ALM.leer('tc', defecto=[]) or []
    return HistorialTC([TC(date.fromisoformat(f), c, v, 'SUNAT PDF') for f, c, v in filas])


def guardar_tc(nuevos: list[TC]):
    d = {f: (c, v) for f, c, v in (ALM.leer('tc', defecto=[]) or [])}
    for t in nuevos:
        d[t.fecha.isoformat()] = (t.compra, t.venta)
    ALM.guardar('tc', [[f, c, v] for f, (c, v) in sorted(d.items())])


def cuentas_banco() -> tuple[dict, dict]:
    """Configuración de cuentas bancarias (editable en Configuración, si no la de ralumin/config.py)."""
    conf = ALM.leer('config:cuentas_banco')
    if not conf:
        return cfg.CUENTAS_POR_NUMERO, cfg.CUENTAS_POR_LETRA
    por_num = {r['numero']: (r['cuenta'], r['moneda'], r['banco']) for r in conf if r.get('numero')}
    por_letra = {(r['banco'], r['letra']): (r['cuenta'], r['moneda']) for r in conf if r.get('letra')}
    return por_num, por_letra


def aplicar_config_cuentas():
    por_num, por_letra = cuentas_banco()
    cfg.CUENTAS_POR_NUMERO = por_num
    cfg.CUENTAS_POR_LETRA = por_letra


aplicar_config_cuentas()

# ----------------------------------------------------------------------------- barra lateral: archivos
with st.sidebar:
    st.header('Archivos')
    docs, resumen_cxp, fecha_cxp = documentos_pendientes()
    if resumen_cxp:
        for r in resumen_cxp:
            st.caption(f"**{r['cuenta']}** · {r['documentos']} docs pendientes · al mes de {r['mes'] or '?'} · cargado {r['cargado'][:16].replace('T', ' ')}")
    else:
        st.warning('Falta la 4212 (análisis de documentos pendientes de NewContaSis).')
    with st.expander('Subir 4212 u otra cuenta por pagar', expanded=not resumen_cxp):
        st.caption('Reporte "Análisis de documentos pendientes" en moneda original. Reemplaza la versión anterior de esa cuenta.')
        subidos = st.file_uploader('Excel de NewContaSis', type=['xlsx'], accept_multiple_files=True, key='up_cxp')
        if subidos and st.button('Guardar cuentas por pagar', type='primary'):
            for f in subidos:
                data = f.getvalue()
                try:
                    d, info = leer_pendientes(data)
                except Exception as ex:
                    st.error(f'{f.name}: no se pudo leer ({ex})')
                    continue
                cuenta = info.get('cuenta') or '4212'
                guardar_archivo(f'archivo:pendientes:{cuenta}', f.name, data, info)
                st.success(f'{f.name}: cuenta {cuenta}, {len(d)} documentos pendientes.')
            st.rerun()

    h = historial_tc()
    st.caption('Tipo de cambio SUNAT: ' + (', '.join(h.meses_cargados()) if len(h) else '—'))
    with st.expander('Subir tipo de cambio SUNAT (PDF del mes)'):
        pdfs = st.file_uploader('PDF de SUNAT', type=['pdf'], accept_multiple_files=True, key='up_tc')
        if pdfs and st.button('Guardar tipo de cambio'):
            for f in pdfs:
                try:
                    guardar_tc(leer_pdf_sunat(f.getvalue()))
                    st.success(f'{f.name}: leído.')
                except Exception as ex:
                    st.error(f'{f.name}: {ex}')
            st.rerun()

    ent_data, ent_meta = leer_archivo('archivo:entidades')
    plan_data, plan_meta = leer_archivo('archivo:plan')
    st.caption(f"Entidades: {ent_meta['fecha'][:10] if ent_meta else '—'} · Plan de cuentas: {plan_meta['fecha'][:10] if plan_meta else '—'}")
    with st.expander('Subir entidades / plan de cuentas'):
        e = st.file_uploader('Entidades (clientes-proveedores)', type=['xlsx'], key='up_ent')
        pl = st.file_uploader('Plan de cuentas', type=['xlsx'], key='up_plan')
        if (e or pl) and st.button('Guardar maestros'):
            if e:
                guardar_archivo('archivo:entidades', e.name, e.getvalue(), {'n': len(leer_entidades(e.getvalue()))})
            if pl:
                guardar_archivo('archivo:plan', pl.name, pl.getvalue())
            st.rerun()

    st.divider()
    st.header('Google Sheet de bancos')
    if not SHEET_ID:
        st.caption('Falta SHEET_ID en los Secrets: suba el Sheet como Excel.')
    if SHEET_ID and st.button('Leer el Sheet ahora', type='primary', width='stretch'):
        try:
            with st.spinner('Descargando el Sheet…'):
                st.session_state['sheet'] = descargar_sheet(SHEET_ID)
                st.session_state['sheet_hora'] = datetime.now().strftime('%H:%M')
        except Exception as ex:
            st.error(f'No se pudo descargar: {ex}. Puede subirlo como Excel (Archivo → Descargar → .xlsx).')
    up_sheet = st.file_uploader('o subir el Sheet como Excel', type=['xlsx'], key='up_sheet')
    if up_sheet:
        st.session_state['sheet'] = up_sheet.getvalue()
        st.session_state['sheet_hora'] = 'archivo ' + up_sheet.name
    if st.session_state.get('sheet'):
        st.caption(f"Sheet leído: {st.session_state.get('sheet_hora')}")
    st.divider()
    st.caption(f'v{__version__} · base: {ALM.descripcion}' + ('' if ALM.persistente else ' (se borra al reiniciar: conectar Neon)'))


# ----------------------------------------------------------------------------- utilidades de tablas
def _f(d) -> str:
    return d.strftime('%d/%m/%Y') if d else ''


def tabla_propuestas(ps) -> pd.DataFrame:
    filas = []
    for p in ps:
        filas.append({
            'Estado': ESTADOS.get(p.estado, p.estado), 'Fecha': p.mov.fecha, 'Pestaña': p.mov.pestana.strip(),
            'Operación': p.mov.operacion, 'Cuenta': p.cuenta, 'Mon.': p.moneda, 'Importe': p.importe,
            'Proveedor': p.proveedor or (p.interp.proveedor_texto if p.interp else ''), 'Documentos': p.documentos,
            'Comisión': p.comision or None,
            'Retención (info)': '; '.join(f'{r.numero} S/ {r.soles:,.2f} → {r.nota}' for r in p.retenciones),
            'TC': p.tc, 'Observaciones': '; '.join(p.motivos + p.notas), 'Sustento': p.mov.sustento, '_id': p.mov.id,
        })
    return pd.DataFrame(filas)


def descargar_lote(lote: dict, asientos):
    imp = excel_importacion(asientos)
    rev = excel_revision(asientos)
    ALM.guardar(f'lote_archivos:{lote["num"]:05d}', dict(importar=base64.b64encode(imp).decode(), revision=base64.b64encode(rev).decode()))


def resolver_dudosos(ps, docs, est):
    """El equipo (o la IA, si hay clave) elige proveedor y documentos; la app lo guarda y lo aprende."""
    pool = [p for p in ps if p.estado == 'revisar']
    if st.toggle('Incluir "fuera de alcance" y adelantos (para enseñarle pagos nuevos: AFP, alquileres…)', key='res_fuera'):
        pool += [p for p in ps if p.estado in ('fuera', 'adelanto')]
    if not pool:
        return
    st.subheader(f'🟡 Resolver pagos dudosos ({len(pool)})')
    st.caption('Elija proveedor y documentos. La app lo aplica a este pago y, si lo marca, lo recuerda para los próximos meses.')
    opciones = {p.mov.id: f"{p.mov.fecha:%d/%m} · {p.mov.pestana.strip()} · {p.importe:,.2f} · {p.mov.sustento[:80]}" for p in pool}
    pid = st.selectbox('Pago', list(opciones), format_func=opciones.get, key='res_pago')
    p = next(x for x in pool if x.mov.id == pid)
    st.markdown(f'**Sustento:** {p.mov.sustento or "(vacío)"}  \n**Banco:** {p.mov.detalle} · operación {p.mov.operacion}')
    if p.motivos:
        st.caption('Motivo: ' + '; '.join(p.motivos))
    if not p.cuenta or not p.tc:
        st.warning('Primero falta la cuenta bancaria configurada o el tipo de cambio de ese día.')
        return
    idx = Indice4212(docs, est['aplicado'])
    clave_sug = f'sug_{pid}'
    if API_IA:
        if st.button('✨ Sugerir con IA', key=f'ia_{pid}'):
            with st.spinner('Consultando…'):
                try:
                    st.session_state[clave_sug] = IA.sugerir(p, idx, API_IA, MODELO_IA)
                except Exception as ex:
                    st.error(f'No se pudo consultar a la IA: {ex}')
    sug = st.session_state.get(clave_sug)
    if sug:
        (st.info if sug.ruc else st.warning)(f"IA ({sug.confianza}%): {sug.razon}" + ('  \n' + '  \n'.join(sug.avisos) if sug.avisos else ''))
    texto = (p.interp.proveedor_texto if p.interp else '') or p.mov.sustento
    ranking = [r for r, _ in sorted(idx.nombres.items(), key=lambda kv: -parecido(texto, kv[1]))]
    preferido = (sug.ruc if sug and sug.ruc else '') or (p.ruc if p.ruc in idx.nombres else '') or (ranking[0] if ranking else '')
    if not ranking:
        st.warning('No hay documentos pendientes cargados.')
        return
    ruc = st.selectbox('Proveedor', ranking, index=ranking.index(preferido) if preferido in ranking else 0,
                       format_func=lambda r: f'{idx.nombres[r]} ({r})', key=f'prov_{pid}')
    sug_l = {n: m for n, m in (sug.lineas if sug and sug.ruc == ruc else [])}
    ds = sorted([d for d in idx.por_ruc.get(ruc, []) if idx.disponible(d) > 0.05], key=lambda d: (d.fecha_doc is None, d.fecha_doc))
    neto = round(p.importe - (p.interp.comision if p.interp else 0), 2)
    filas = []
    for d in ds:
        en_cta = _de_moneda_doc(idx.disponible(d), p, d)
        incluir = d.numero in sug_l or (not sug_l and abs(en_cta - neto) <= 0.05)
        filas.append({'Incluir': incluir, 'Documento': d.numero, 'Cuenta': d.cuenta, 'Fecha': d.fecha_doc,
                      'Mon.': d.moneda, 'Saldo': idx.disponible(d), f'Pagar ({p.moneda})': sug_l.get(d.numero, en_cta if incluir else 0.0)})
    if not filas:
        st.warning('Ese proveedor no tiene documentos pendientes.')
        return
    ed = st.data_editor(pd.DataFrame(filas), hide_index=True, width='stretch', key=f'docs_{pid}_{ruc}',
                        disabled=['Documento', 'Cuenta', 'Fecha', 'Mon.', 'Saldo'],
                        column_config={'Fecha': st.column_config.DateColumn(format='DD/MM/YYYY')})
    col_pago = f'Pagar ({p.moneda})'
    c1, c2 = st.columns(2)
    com = c1.number_input('Comisión del banco', min_value=0.0, step=0.01, format='%.2f', key=f'com_{pid}',
                          value=float(sug.comision if sug and sug.ruc else (p.interp.comision if p.interp else 0.0)))
    elegidas = [[r['Documento'], round(float(r[col_pago] or 0), 2)] for r in ed.to_dict('records') if r['Incluir'] and r[col_pago]]
    dif = round(p.importe - sum(x for _, x in elegidas) - com, 2)
    c2.metric('Diferencia con el banco', f'{dif:,.2f}')
    recordar = st.checkbox('Recordar este proveedor para este texto del sustento', value=True, key=f'rec_{pid}')
    modo = st.radio('En los próximos meses, para pagos con el mismo texto (sin importar números ni meses):',
                    ['No', 'Elegir el documento con saldo igual al pago', 'Elegir los documentos más antiguos'],
                    horizontal=True, key=f'modo_{pid}')
    if st.button('Guardar solución', type='primary', disabled=(abs(dif) > 0.01 or not elegidas), key=f'guardar_{pid}'):
        MEM.aprender(mov_id=p.mov.id, firma=L.firma_mov(p.mov), sustento=p.mov.sustento,
                     proveedor_texto=texto if p.interp and p.interp.proveedor_texto else '', ruc=ruc, nombre=idx.nombres[ruc],
                     lineas=elegidas, comision=com, origen='ia' if sug and sug.ruc == ruc else 'equipo',
                     recordar_alias=recordar, modo_patron={'No': '', 'Elegir el documento con saldo igual al pago': 'monto'}.get(modo, 'antiguo'))
        st.session_state.pop(clave_sug, None)
        st.success('Guardado: el pago pasa a 🟢 Listo.')
        st.rerun()


# ----------------------------------------------------------------------------- pantalla principal
st.title('Pagos Ralumin')
st.markdown('<span class="pequeno">Banco (Google Sheet) × cuentas por pagar → plantilla de "Importación de Pagos" de NewContaSis, por lotes.</span>',
            unsafe_allow_html=True)
lotes = L.listar(ALM)
t_pagos, t_ret, t_lotes, t_conf = st.tabs(['💸 Pagos', '🧾 Retenciones', '📦 Lotes', '⚙️ Configuración'])

# ============================================================================= PAGOS
with t_pagos:
    if not st.session_state.get('sheet'):
        st.info('Lea el Google Sheet de bancos (barra lateral) para ver los pagos.')
    elif not docs:
        st.info('Suba la 4212 (barra lateral).')
    else:
        libro = _leer_libro_cache(_h(st.session_state['sheet']), st.session_state['sheet'])
        tabs = pestanas_con_bancos(libro)
        if not tabs:
            st.error('No se encontraron pestañas de bancos (por ejemplo "BCP-OCT-26").')
            st.stop()
        _, a0, m0 = info_pestana(tabs[0])
        por_defecto = [t for t in tabs if info_pestana(t)[1:] == (a0, m0)]
        elegidas = st.multiselect('Pestañas del Sheet', tabs, default=por_defecto)
        movs = [m for t in elegidas for m in leer_pestana(t, libro[t])]
        est = L.estado(lotes, 'pagos', fecha_cxp)
        ps = proponer(movs, docs, historial_tc(), aplicado=est['aplicado'], registrados_app=est['registrados'],
                      en_lote=est['en_lote'], memoria=MEM)
        cont = pd.Series([p.estado for p in ps]).value_counts() if ps else pd.Series(dtype=int)
        cols = st.columns(6)
        for c, k in zip(cols, ['listo', 'revisar', 'adelanto', 'en lote', 'registrado', 'fuera']):
            c.metric(ESTADOS[k], int(cont.get(k, 0)))

        st.subheader('Armar un lote')
        listos = [p for p in ps if p.estado == 'listo']
        if not listos:
            st.success('No hay pagos listos pendientes en las pestañas elegidas.')
        else:
            fechas = sorted({p.mov.fecha for p in listos})
            c1, c2, c3 = st.columns([2, 2, 3])
            rango = c1.date_input('Días', value=(fechas[0], fechas[-1]), min_value=fechas[0], max_value=fechas[-1], format='DD/MM/YYYY')
            ctas = sorted({p.cuenta for p in listos})
            f_ctas = c2.multiselect('Cuenta bancaria', ctas, default=ctas)
            provs = sorted({p.proveedor for p in listos})
            f_prov = c3.multiselect('Proveedor (RUC / razón social)', provs, placeholder='Todos')
            d1, d2 = (rango if isinstance(rango, (list, tuple)) and len(rango) == 2 else (rango[0], rango[0]) if isinstance(rango, (list, tuple)) else (rango, rango))
            sel = [p for p in listos if d1 <= p.mov.fecha <= d2 and p.cuenta in f_ctas and (not f_prov or p.proveedor in f_prov)]
            df = tabla_propuestas(sel)
            df.insert(0, 'Incluir', True)
            ed = st.data_editor(df.drop(columns=['Estado', 'Sustento']), hide_index=True, width='stretch',
                                disabled=[c for c in df.columns if c != 'Incluir'],
                                column_config={'_id': None, 'Fecha': st.column_config.DateColumn(format='DD/MM/YYYY'),
                                               'Importe': st.column_config.NumberColumn(format='%.2f'),
                                               'Comisión': st.column_config.NumberColumn(format='%.2f'),
                                               'TC': st.column_config.NumberColumn(format='%.3f')}, key='ed_pagos')
            ids = set(ed.loc[ed['Incluir'], '_id']) if len(ed) else set()
            elegidos = [p for p in sel if p.mov.id in ids]
            tot = {}
            for p in elegidos:
                tot[p.cuenta] = tot.get(p.cuenta, 0) + p.importe
            st.caption(f'{len(elegidos)} pagos · ' + ' · '.join(f'{c}: {v:,.2f}' for c, v in sorted(tot.items())))
            desc = st.text_input('Descripción del lote', value=f'Pagos {d1:%d/%m}–{d2:%d/%m}', key='desc_pagos')
            if st.button(f'Crear lote con {len(elegidos)} pagos', type='primary', disabled=not elegidos):
                asientos, _ = asientos_de_pagos(elegidos, est['usados_bc'])
                errores = [f'{a.c}: {e}' for a in asientos for e in a.errores()]
                if errores:
                    st.error('No se creó el lote:\n\n' + '\n\n'.join(errores))
                else:
                    info = {p.mov.id: dict(pestana=p.mov.pestana, fila=p.mov.fila, col_asiento=p.mov.col_asiento,
                                           firma=L.firma_mov(p.mov), operacion=p.mov.operacion) for p in elegidos}
                    lote = L.crear(ALM, 'pagos', asientos, info, desc, cfg.SUBDIARIO)
                    descargar_lote(lote, asientos)
                    st.success(f'Lote {lote["num"]} creado. Descárguelo en la pestaña Lotes.')
                    st.rerun()

        resolver_dudosos(ps, docs, est)
        for k, titulo in (('revisar', '🟡 Por revisar'), ('adelanto', '🔵 Adelantos de importación'),
                          ('en lote', '📦 En lotes sin confirmar'), ('fuera', '⚪ Fuera de alcance'), ('registrado', '✅ Ya registrados')):
            grupo = [p for p in ps if p.estado == k]
            if grupo:
                with st.expander(f'{titulo} ({len(grupo)})', expanded=False):
                    st.dataframe(tabla_propuestas(grupo).drop(columns=['Estado', '_id']), hide_index=True, width='stretch',
                                 column_config={'Fecha': st.column_config.DateColumn(format='DD/MM/YYYY')})

# ============================================================================= RETENCIONES
with t_ret:
    st.caption('Excel de SUNAT "Consulta de Comprobantes Emitidos" (retenciones) × 4212 → un asiento por comprobante: '
               '4212 al Debe, 401141 al Haber (sin 104). En dólares: soles ÷ TC venta SUNAT de la fecha del comprobante.')
    up = st.file_uploader('Retenciones del mes (.xls / .xlsx)', type=['xls', 'xlsx'], accept_multiple_files=True, key='up_ret')
    if up and st.button('Guardar retenciones'):
        for f in up:
            crs, av = leer_excel_retenciones(f.getvalue(), f.name)
            if not crs:
                st.error(f'{f.name}: no se encontraron comprobantes.')
                continue
            mes = max(c.fecha for c in crs)[:7]
            guardar_archivo(f'archivo:retenciones:{mes}', f.name, f.getvalue(), {'comprobantes': len(crs)})
            st.success(f'{f.name}: {len(crs)} comprobantes ({mes}).')
        st.rerun()
    claves_ret = ALM.claves('archivo:retenciones:')
    if not claves_ret:
        st.info('Suba el Excel de retenciones del mes.')
    elif not docs:
        st.info('Suba la 4212.')
    else:
        meses = [k.split(':')[-1] for k in claves_ret]
        mes = st.selectbox('Mes', sorted(meses, reverse=True))
        data, meta = leer_archivo(f'archivo:retenciones:{mes}')
        crs, _ = _leer_ret_cache(_h(data), data, meta['nombre'])
        est_r = L.estado(lotes, 'retenciones', fecha_cxp)
        prs = proponer_retenciones(crs, docs, historial_tc(), ya=est_r['registrados'], en_lote=est_r['en_lote'],
                                   aplicado=est_r['aplicado'])
        cont = pd.Series([p.estado for p in prs]).value_counts()
        cols = st.columns(5)
        for c, k in zip(cols, ['listo', 'revisar', 'sin saldo', 'en lote', 'registrado']):
            c.metric(ESTADOS[k], int(cont.get(k, 0)))
        filas = [{'Incluir': p.estado == 'listo', 'Estado': ESTADOS.get(p.estado, p.estado), 'Fecha': p.fecha, 'Comprobante': p.numero,
                  'Proveedor': p.proveedor, 'Retención S/': p.total_soles, 'Mon.': p.moneda, 'TC': p.tc,
                  'Retención US$': round(sum(l['dolares'] for l in p.lineas), 2) if p.moneda == 'D' else None,
                  'Facturas': p.facturas, 'Observaciones': '; '.join(p.motivos + p.notas), '_id': p.id} for p in prs]
        df = pd.DataFrame(filas)
        ed = st.data_editor(df, hide_index=True, width='stretch', key='ed_ret',
                            disabled=[c for c in df.columns if c != 'Incluir'],
                            column_config={'_id': None, 'Fecha': st.column_config.DateColumn(format='DD/MM/YYYY'),
                                           'TC': st.column_config.NumberColumn(format='%.3f')})
        ids = set(ed.loc[ed['Incluir'], '_id'])
        elegidos = [p for p in prs if p.id in ids and p.estado == 'listo']
        st.caption(f'{len(elegidos)} comprobantes listos seleccionados (solo se incluyen los 🟢).')
        if st.button(f'Crear lote de retenciones ({len(elegidos)})', type='primary', disabled=not elegidos):
            asientos = asientos_de_retenciones(elegidos)
            errores = [f'{a.c}: {e}' for a in asientos for e in a.errores()]
            if errores:
                st.error('\n\n'.join(errores))
            else:
                lote = L.crear(ALM, 'retenciones', asientos, {}, f'Retenciones {mes}', cfg.SUBDIARIO)
                descargar_lote(lote, asientos)
                st.success(f'Lote {lote["num"]} creado. Descárguelo en la pestaña Lotes.')
                st.rerun()

# ============================================================================= LOTES
with t_lotes:
    if not lotes:
        st.info('Todavía no hay lotes.')
    for lote in lotes:
        icono = {'exportado': '📦', 'registrado': '✅', 'anulado': '⚪'}[lote['estado']]
        total = sum(it['importe'] for it in lote['items'])
        with st.expander(f"{icono} Lote {lote['num']} · {lote['tipo']} · {lote['descripcion']} · {len(lote['items'])} asientos · "
                         f"{lote['estado']} · {lote['creado'][:16].replace('T', ' ')}", expanded=lote['estado'] == 'exportado'):
            arch = ALM.leer(f'lote_archivos:{lote["num"]:05d}') or {}
            c1, c2 = st.columns(2)
            if arch:
                c1.download_button('⬇️ Excel para importar', base64.b64decode(arch['importar']),
                                   file_name=f'PAGOS_RALUMIN_LOTE_{lote["num"]:03d}.xlsx', key=f'dl_i_{lote["num"]}')
                c2.download_button('⬇️ Excel de revisión', base64.b64decode(arch['revision']),
                                   file_name=f'REVISION_LOTE_{lote["num"]:03d}.xlsx', key=f'dl_r_{lote["num"]}')
            st.dataframe(pd.DataFrame([{'Orden': it['orden'] + 1, 'Asiento': it['asiento'], 'Fecha': it['fecha'], 'B': it['b'], 'C': it['c'],
                                        'Proveedor': it['proveedor'], 'Cuenta': it['cuenta'], 'Importe': it['importe'],
                                        'Fila Sheet': f"{it.get('pestana', '')} {it.get('fila', '')}".strip()} for it in lote['items']]),
                         hide_index=True, width='stretch')
            if lote['estado'] == 'exportado':
                st.markdown('**Después de importar en NewContaSis** (Sub Diario 04 · L/Registro 01 · F/Efectivo F055), '
                            'anote el "Asiento Inicio" que muestra la pantalla de importación.')
                c1, c2, c3 = st.columns([1, 1, 2])
                ini = c1.number_input('Asiento Inicio', min_value=1, step=1, key=f'ini_{lote["num"]}')
                sub = c2.text_input('Subdiario', value=lote.get('subdiario') or cfg.SUBDIARIO, key=f'sub_{lote["num"]}')
                if c3.button('Confirmar registrado', type='primary', key=f'conf_{lote["num"]}'):
                    L.confirmar(ALM, lote['num'], int(ini), sub.strip() or cfg.SUBDIARIO)
                    st.rerun()
                if st.button('Anular lote (la importación falló o se descarta)', key=f'anu_{lote["num"]}'):
                    L.anular(ALM, lote['num'])
                    st.rerun()
            elif lote['estado'] == 'registrado' and lote['tipo'] == 'pagos':
                items = [it for it in lote['items'] if it.get('fila')]
                cuenta_serv = secreto('GOOGLE_SERVICE_ACCOUNT')
                if lote.get('sheet_escrito'):
                    st.caption('✅ Asientos escritos en el Google Sheet.')
                elif cuenta_serv:
                    if st.button('Escribir los asientos en el Google Sheet', key=f'sheet_{lote["num"]}'):
                        from motor.sheet import escribir_asientos
                        try:
                            n = escribir_asientos(cuenta_serv, SHEET_ID, items)
                            L.marcar_sheet(ALM, lote['num'])
                            st.success(f'{n} celdas escritas.')
                            st.rerun()
                        except Exception as ex:
                            st.error(f'No se pudo escribir en el Sheet: {ex}')
                else:
                    st.caption('Para que el app escriba solo el asiento en el Sheet falta la cuenta de servicio de Google. '
                               'Mientras tanto, copie esta lista:')
                    st.dataframe(pd.DataFrame([{'Pestaña': it['pestana'], 'Fila': it['fila'], 'Asiento': it['asiento'],
                                                'Operación': it.get('operacion', ''), 'Importe': it['importe']} for it in items]),
                                 hide_index=True)

# ============================================================================= CONFIGURACIÓN
with t_conf:
    st.subheader('Cuentas bancarias')
    st.caption('Cómo se reconoce cada cuenta del Sheet: por número de cuenta o, si la pestaña no lo trae, por banco + letra (columna CUENTA (ABC)).')
    por_num, por_letra = cuentas_banco()
    filas = [dict(banco=b, numero=n, letra='', cuenta=c, moneda=m) for n, (c, m, b) in por_num.items()]
    filas += [dict(banco=b, numero='', letra=l, cuenta=c, moneda=m) for (b, l), (c, m) in por_letra.items()]
    ed = st.data_editor(pd.DataFrame(filas), num_rows='dynamic', hide_index=True, width='stretch', key='ed_ctas',
                        column_config={'moneda': st.column_config.SelectboxColumn(options=['D', 'S']),
                                       'banco': st.column_config.SelectboxColumn(options=['BCP', 'BBVA', 'SCOTIA'])})
    if st.button('Guardar cuentas bancarias'):
        ALM.guardar('config:cuentas_banco', [{k: (str(v).strip() if v is not None else '') for k, v in r.items()}
                                             for r in ed.to_dict('records') if r.get('cuenta')])
        st.success('Guardado.')
        st.rerun()
    st.subheader('Lo aprendido')
    st.caption(f"{len(MEM.alias)} proveedores reconocidos · {len(MEM.patrones)} patrones mensuales · "
               f"{len({id(v) for v in MEM.soluciones.values()})} pagos resueltos a mano. IA: {'activa (' + MODELO_IA + ')' if API_IA else 'sin clave'}.")
    if MEM.alias:
        st.dataframe(pd.DataFrame([{'Texto del Sheet': v.get('texto', k), 'Proveedor': v['nombre'], 'RUC': v['ruc'], 'Origen': v['origen'],
                                    'Fecha': v['creado'][:10]} for k, v in MEM.alias.items()]), hide_index=True, width='stretch')
        borrar = st.multiselect('Olvidar proveedores', list(MEM.alias), format_func=lambda k: MEM.alias[k].get('texto', k), key='olv_alias')
        if borrar and st.button('Olvidar seleccionados', key='btn_olv_alias'):
            MEM.olvidar('alias', borrar)
            st.rerun()
    if MEM.patrones:
        st.dataframe(pd.DataFrame([{'Patrón': k, 'Proveedor': v['nombre'], 'Regla': 'saldo igual al pago' if v['modo'] == 'monto' else 'más antiguos',
                                    'Ejemplo': v['ejemplo'], 'Fecha': v['creado'][:10]} for k, v in MEM.patrones.items()]), hide_index=True, width='stretch')
        borrar = st.multiselect('Olvidar patrones', list(MEM.patrones), key='olv_pat')
        if borrar and st.button('Olvidar seleccionados', key='btn_olv_pat'):
            MEM.olvidar('patrones', borrar)
            st.rerun()
    st.subheader('Reglas fijas')
    st.markdown(f"""
- Comisión bancaria: **{cfg.CUENTA_COMISION}**, línea dentro del mismo pago (una sola 104 por cargo del banco).
- Retención del sustento: **informativa** (no genera asiento en pagos). Las retenciones van en su propio lote: 4212 / **{cfg.CUENTA_RETENCIONES}**.
- B/C: tipo y número de la primera factura; si esa factura ya se usó en otra operación → **13 + n° de operación**.
- Importación: Sub Diario **{cfg.SUBDIARIO}**, L/Registro **{cfg.LIBRO}**, F/Efectivo **{cfg.FLUJO_EFECTIVO}**. CALLE 23 → centro de costo **0101**.
""")
