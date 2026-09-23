# Registro de gastos

Descarga todos los días los movimientos de **Santander** (cuenta y **Visa**) y **Mercado Pago**,
los guarda en una base local, los **categoriza** y arma el registro de **ingresos y egresos**
por categoría y por mes (en pantalla y en Excel).

```
          ┌──────────────────────┐
Mercado   │ API oficial (reporte │──┐
Pago  ───▶│ de dinero liberado)  │  │
          └──────────────────────┘  │     ┌──────────┐   ┌──────────────┐   ┌──────────────────┐
          ┌──────────────────────┐  ├────▶│ SQLite   │──▶│ Reglas de    │──▶│ resumen / Excel  │
Santander │ navegador automático │  │     │ sin dupl.│   │ categorías   │   │ ingresos-egresos │
cuenta y ▶│ (Playwright) o archi-│──┘     └──────────┘   └──────────────┘   └──────────────────┘
Visa      │ vo que bajás a mano  │
          └──────────────────────┘
```

## Cómo se conecta a cada entidad

| Entidad | Cómo | Automático |
|---|---|---|
| **Mercado Pago** | [API oficial de reportes](https://www.mercadopago.com.ar/developers/es/docs/reports/released-money/api) con tu Access Token | ✅ 100 % |
| **Santander cuenta** | Santander Argentina **no tiene API para personas**. Dos opciones: (a) el script maneja un navegador y baja el Excel de movimientos, o (b) lo bajás a mano y lo dejás en una carpeta | ⚠️ (a) sí, pero la primera vez (y cada tanto) te pide el token |
| **Visa Santander** | Igual que la cuenta: "Últimos consumos" o el resumen en Excel/CSV | ⚠️ ídem |

Sobre la descarga con navegador: los pasos (qué clickear) se definen en `config.yaml`, no en el
código, porque el sitio del banco cambia y cada uno tiene menús distintos. Usa un perfil de
navegador persistente, para que Santander "recuerde el dispositivo" y no pida el token todos los días.
Si en algún momento lo pide y el script está corriendo solo (cron), falla con un aviso claro y
lo resolvés corriendo `gastos sync --cuenta santander_pesos` desde una terminal.

> Guardar la clave del banco en un archivo local tiene riesgo. Queda en `.env` (fuera del repo),
> en tu máquina. Si preferís no hacerlo, no configures `descarga_web`: con que tires el archivo
> que bajás de Online Banking en la carpeta, el resto sigue siendo automático.

## Instalación

```bash
git clone <este repo> && cd registro-de-gastos
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[web]"                                   # sin [web] si no vas a usar el navegador
playwright install chromium                               # solo para la descarga automática
cp config.example.yaml config.yaml
cp .env.example .env
```

### 1. Mercado Pago (5 minutos)

1. Entrá a <https://www.mercadopago.com.ar/developers/panel/app> y creá una aplicación
   (cualquier nombre, p. ej. "registro-gastos").
2. En **Credenciales de producción** copiá el **Access Token** (`APP_USR-...`) a `.env` como
   `MP_ACCESS_TOKEN`.
3. `gastos mp-config` (configura las columnas del reporte; se hace una sola vez).
4. `gastos sync --cuenta mercadopago` → la primera vez trae los últimos 90 días.

El reporte tarda desde unos segundos hasta un par de minutos en generarse; el script espera.

### 2. Santander (cuenta y Visa)

**Opción manual (recomendada para empezar):** desde Online Banking descargá los movimientos de
la cuenta en Excel/CSV y guardalos en `data/bandeja/santander_pesos/`. Lo mismo con la Visa
("Tarjetas → Últimos consumos" o el resumen) en `data/bandeja/visa_santander/`. Después:

```bash
gastos sync
```

El importador detecta solo la fila de encabezados y las columnas (Fecha, Descripción/Concepto,
Importe o Débito/Crédito, columnas separadas de pesos y dólares). Si algo no lo reconoce, te
dice qué columnas encontró y las podés indicar a mano en `config.yaml` (`columnas`,
`fila_encabezado`, `montos_por_moneda`). Los archivos importados se mueven a `data/procesados/`.
Podés bajar períodos que se superponen: los duplicados se descartan.

**Opción automática:**

1. Completá `SANTANDER_DNI`, `SANTANDER_CLAVE`, `SANTANDER_USUARIO` en `.env`.
2. Corré `gastos grabar https://www.santander.com.ar/banco/online/personas` — se abre un
   navegador; hacé el recorrido hasta descargar el Excel. La ventana del inspector te muestra
   los selectores de cada click.
3. Pasá esos selectores al bloque `descarga_web` de la cuenta en `config.yaml` (hay un ejemplo
   comentado).
4. La primera vez corré `gastos sync --cuenta santander_pesos` con `headless: false` para
   completar el token. Después podés pasar a `headless: true`.

Si un paso falla, se guarda una captura de pantalla en `data/logs/` para ver qué pasó.

## Uso diario

```bash
gastos sync                 # descarga/importa todo y categoriza
gastos resumen              # ingresos y egresos por categoría, últimos 3 meses
gastos resumen --meses 12
gastos movimientos --sin-categoria
gastos categorizar          # interactivo: asignás categoría y aprende la regla
gastos asignar 3e977ef1 Regalos    # corrige un movimiento puntual (no se pisa)
gastos exportar --meses 12  # Excel en data/reportes/gastos.xlsx
```

Ejemplo de `gastos resumen`:

```
=== ARS ===
mes                                   2026-09
Ingresos Sueldo                  1,250,000.50
Egresos  Servicios del hogar        45,321.99
         Supermercado               25,000.00
         Mascotas                    8,000.00
         Impuestos y retenciones     2,100.00
TOTAL    Ingresos                1,250,000.50
         Egresos                    80,421.99
         Balance (ahorro)        1,169,578.51

(2 movimientos internos excluidos: pagos de tarjeta, transferencias entre cuentas propias, etc.)
```

### Automatizarlo todos los días

- **Linux/Mac (cron):** ver `deploy/crontab.example` → corre `scripts/sync_diario.sh` a las 7:30,
  deja log en `data/logs/` y regenera el Excel.
- **Linux (systemd):** `deploy/gastos-sync.service` + `deploy/gastos-sync.timer`.
- **Windows:** `deploy/windows_tarea.ps1` crea una tarea programada.

`gastos sync` termina con código de error si alguna cuenta falló (sin frenar a las demás), así
el programador de tareas lo puede avisar.

## Categorías

- `categorias.yaml`: reglas generales ya cargadas con comercios y conceptos típicos de
  Argentina (supermercados, servicios, impuesto al cheque, percepciones, suscripciones, etc.).
- `reglas_personales.yaml`: se crea con `gastos categorizar`, tiene prioridad y no se sube al repo.

Cada categoría tiene un **tipo**: `ingreso`, `egreso` o `interno`. Los **internos** no suman ni
restan, y son clave para no contar dos veces la misma plata:

- **Pago de la Visa** desde la cuenta: el gasto real son los consumos de la tarjeta; el pago es
  mover plata de un lado al otro.
- **Transferencias Santander ↔ Mercado Pago**: agregá tu nombre (como aparece en las
  transferencias) a la categoría "Transferencia entre cuentas propias".
- Compra de dólares, plazos fijos, FCI.

Un reintegro que cae en una categoría de egreso resta del gasto de esa categoría.

Si editás las reglas, `gastos recategorizar` las reaplica a todo el historial (menos lo que
asignaste a mano).

## Detalles

- Todo queda en `data/gastos.db` (SQLite). Hacé backup de esa carpeta; está en `.gitignore`.
- Pesos y dólares se reportan por separado (no se convierten).
- Compras en cuotas: se registra cada cuota cuando aparece en la tarjeta.
- Mercado Pago: solo trae lo que pasa por el **dinero en cuenta**. Si pagás con la Visa a
  través de Mercado Pago, ese gasto no toca tu saldo, así que debería aparecer solo en la Visa
  (y no duplicarse).

## Tests

```bash
pip install -e ".[dev,web]"
pytest
```
