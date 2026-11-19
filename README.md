# Pagos Ralumin

App de pagos **solo para RALUMIN E.I.R.L.** (RUC 20602768440). Cruza los egresos del Google Sheet de bancos
("PAGOS RALUMIN") con la **4212** (u otra cuenta por pagar) exportada de NewContaSis y entrega, **por lotes**, el Excel
listo para **Importación de Pagos** (plantilla FORMATO_PAGOS, columnas A–W). También arma el lote de **retenciones**
del mes (4212 al Debe, 401141 al Haber, sin 104) desde el Excel de SUNAT.

```
Google Sheet (pestaña banco-mes) ─▶ egresos sin asiento en la columna de control
   ─▶ sustento: proveedor, facturas, montos, comisión, retención (informativa)
   ─▶ cruce con la 4212 (RUC/razón social + serie-número) ─▶ TC venta SUNAT del día ─▶ validaciones
   ─▶ 🟢 listo / 🟡 revisar / 🔵 adelanto / ⚪ fuera de alcance / ✅ registrado
   ─▶ lote (días, cuenta, proveedor o todo) ─▶ Excel ─▶ NewContaSis ─▶ "Asiento Inicio" ─▶ 04-N en el Sheet
```

## Cómo se usa

1. **Barra lateral → Archivos:** subir la **4212** ("Análisis de documentos pendientes", en moneda original) cada vez que
   se actualice; el PDF de **tipo de cambio SUNAT** del mes; cuando cambien, el **plan de cuentas** y las **entidades**.
   El app guarda la última versión de cada uno.
2. **Leer el Sheet ahora** (o subirlo como Excel).
3. **💸 Pagos:** elegir pestañas, filtrar por días, cuenta bancaria o proveedor, desmarcar lo que no va y **Crear lote**.
4. **📦 Lotes:** descargar el Excel, importarlo en NewContaSis (Sub Diario 04 · L/Registro 01 · F/Efectivo F055) y anotar
   el **Asiento Inicio**: el app asigna el 04-N de cada pago y lo escribe en la columna de control del Sheet.
   Si la importación falla: **Anular lote** y los pagos vuelven a estar disponibles.
5. **🧾 Retenciones:** subir el Excel de SUNAT "Consulta de Comprobantes Emitidos" del mes → lote con un asiento por comprobante.

## Reglas validadas con importaciones reales (18/11/2026)

| Tema | Regla |
|---|---|
| Archivo | Libro limpio, solo filas de datos (sin las 13 filas de instrucciones ni filas vacías con formato). |
| Columna I | **Importe de cada línea** (= R en dólares, = Q en soles). NewContaSis suma I por documento de pago para armar UNA línea 104. |
| Cuadre | NewContaSis no bloquea asientos descuadrados: el app valida cada pago antes de exportar. |
| B / C | Tipo y número de la primera factura pagada. Si esa factura ya se usó en otra operación (mismo lote o anterior): **13 + n° de operación**. |
| Comisión | Línea dentro del mismo pago, cuenta **6391094**, entidad = banco, documento 13 + n° de operación. |
| Retención del sustento | **Informativa**: no genera asiento en pagos. Se valida que pago + retención no supere el saldo de la factura. |
| Lote de retenciones | B = 20, C = n° de comprobante, G = **401141**; en dólares: soles ÷ TC venta SUNAT de la fecha del comprobante. |
| Tipo de cambio | Venta SUNAT del día de pago (sin publicación: el día anterior). |
| Numeración | NewContaSis numera los asientos ordenando por B y luego C: el Excel sale en ese orden. |
| Columna de control | La que sigue a CARGO / ABONO en cada pestaña: si tiene cualquier valor, el movimiento ya está registrado. |
| Cuentas bancarias | 104102 BCP ME · 104103 BCP MN · 104101 caja chica · 104104 Scotiabank ME · 104105 BBVA MN (editable en ⚙️). |

## Lo que la app aprende (v0.2.0)

Tres niveles, en este orden:

1. **Reglas fijas** (arriba).
2. **Memoria del equipo** (se guarda en la base, a la vista en ⚙️ Configuración → Lo aprendido):
   en **💸 Pagos → Resolver pagos dudosos** se elige proveedor y documentos de un pago 🟡; la app lo aplica y recuerda
   el **proveedor para ese texto** (alias) y, si se marca, un **patrón mensual** para pagos con el mismo texto
   (sin importar números ni meses): elegir el documento con saldo igual al pago, o los más antiguos.
3. **IA solo en casos dudosos** (si hay `ANTHROPIC_API_KEY`): botón **✨ Sugerir con IA**. Elige solo entre proveedores
   y documentos pendientes reales; lo inventado se descarta y se avisa si no cuadra con el banco. Nunca entra sola a un
   lote: el equipo acepta o corrige, y lo aceptado pasa a la memoria (la próxima vez no se consulta a la IA).

Reglas en `ralumin/config.py`. Lo que el app todavía no sabe cruzar (planilla, AFP, impuestos, adelantos…) queda a la
vista en **Por revisar / Fuera de alcance** con su motivo, para ir agregando reglas por bloques.

## Instalación

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # poner APP_PASSWORD (y DATABASE_URL de Neon)
streamlit run app.py
```

En Streamlit Cloud: New app → este repositorio → `app.py`; en **Secrets** pegar `APP_PASSWORD` y, al final, `DATABASE_URL`
de Neon (sin Neon los lotes se pierden cuando Streamlit reinicia).

Pruebas: `pytest -q` (la de regresión con datos reales de Ralumin necesita `RALUMIN_DATOS=/carpeta`).
