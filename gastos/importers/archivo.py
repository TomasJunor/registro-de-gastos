"""Importador genérico de extractos en CSV / XLSX / XLS.

Sirve para lo que descargues desde Online Banking Santander (movimientos de la
cuenta, últimos consumos de la Visa) o el reporte de "Actividad" de Mercado
Pago. Detecta solo la fila de encabezados y las columnas usando sinónimos
habituales; si tu archivo es raro, podés indicar las columnas a mano en
config.yaml (ver `columnas` y `montos_por_moneda`).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd

from gastos.models import Movimiento
from gastos.parsing import es_vacio, normalizar, parse_fecha, parse_importe

SINONIMOS = {
    "fecha": ["fecha", "fecha operacion", "fecha de operacion", "fecha movimiento",
              "fecha de movimiento", "fecha origen", "fecha consumo", "fecha de consumo",
              "fecha de compra", "date", "fecha de liberacion", "fecha de creacion"],
    "descripcion": ["descripcion", "concepto", "detalle", "descripcion del movimiento",
                    "movimiento", "establecimiento", "comercio", "description", "operacion"],
    "importe": ["importe", "monto", "importe total", "valor", "amount", "neto", "monto neto",
                "monto bruto"],
    "debito": ["debito", "debitos", "debe", "egreso", "egresos", "retiro"],
    "credito": ["credito", "creditos", "haber", "ingreso", "ingresos", "deposito"],
    "moneda": ["moneda", "currency", "divisa"],
}
MARCAS_USD = ("dolar", "u$s", "usd", "us$", "u$d")
MARCAS_ARS = ("peso", "$", "ars")
EXTENSIONES = {".csv", ".txt", ".xlsx", ".xlsm", ".xls"}


class ErrorFormato(ValueError):
    pass


def importar_archivo(ruta: str | Path, cuenta: dict) -> list[Movimiento]:
    ruta = Path(ruta)
    tabla = leer_tabla(ruta)
    fila_enc = cuenta.get("fila_encabezado")
    if fila_enc is None:
        fila_enc = _buscar_encabezado(tabla)
    encabezados = [str(x).strip() if not es_vacio(x) else f"col{i}"
                   for i, x in enumerate(tabla[fila_enc])]
    cols = _mapear_columnas(encabezados, cuenta)

    decimal = cuenta.get("decimal", ",")
    dayfirst = cuenta.get("dia_primero", True)
    signo = -1 if cuenta.get("invertir_signo") else 1
    moneda_default = cuenta.get("moneda", "ARS")

    movimientos = []
    for fila in tabla[fila_enc + 1:]:
        fila = list(fila) + [None] * (len(encabezados) - len(fila))
        try:
            fecha = parse_fecha(fila[cols["fecha"]], dayfirst=dayfirst)
        except ValueError:
            fecha = None
        if fecha is None:
            continue  # totales, líneas en blanco, "saldo anterior", etc.

        descripcion = " ".join(
            str(fila[i]).strip() for i in cols["descripcion"] if not es_vacio(fila[i])
        ) or "(sin descripción)"
        extra = {
            encabezados[i]: str(v).strip() for i, v in enumerate(fila)
            if i not in cols["usadas"] and not es_vacio(v)
        }

        for moneda, monto in _montos(fila, cols, decimal, moneda_default):
            if monto is None or abs(monto) < 0.005:
                continue
            movimientos.append(Movimiento(
                cuenta=cuenta["id"], fecha=fecha, descripcion=descripcion,
                monto=signo * monto, moneda=moneda, origen=ruta.name, extra=extra,
            ))
    return movimientos


# --- lectura -------------------------------------------------------------------

def leer_tabla(ruta: Path) -> list[list]:
    """Devuelve el archivo como lista de filas (sin interpretar encabezados)."""
    ext = ruta.suffix.lower()
    if ext in (".csv", ".txt"):
        return _leer_csv(ruta)
    if ext in (".xlsx", ".xlsm"):
        df = pd.read_excel(ruta, header=None, dtype=object, engine="openpyxl")
        return df.values.tolist()
    if ext == ".xls":
        try:
            df = pd.read_excel(ruta, header=None, dtype=object, engine="xlrd")
            return df.values.tolist()
        except Exception:
            # Muchos bancos exportan HTML con extensión .xls
            tablas = pd.read_html(ruta, header=None, thousands=None, decimal="|")
            if not tablas:
                raise ErrorFormato(f"{ruta.name}: no pude leerlo como Excel ni como HTML")
            df = max(tablas, key=len)
            return df.astype(object).values.tolist()
    raise ErrorFormato(f"{ruta.name}: extensión no soportada ({ext})")


def _leer_csv(ruta: Path) -> list[list]:
    crudo = ruta.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = crudo.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialecto = csv.Sniffer().sniff(texto[:5000], delimiters=";,\t|")
        sep = dialecto.delimiter
    except csv.Error:
        sep = ";" if texto.count(";") > texto.count(",") else ","
    return [fila for fila in csv.reader(io.StringIO(texto), delimiter=sep)]


# --- detección de columnas -----------------------------------------------------

def _es(encabezado: str, campo: str) -> bool:
    n = normalizar(encabezado).rstrip(":")
    return any(n == s or n.startswith(s + " ") for s in SINONIMOS[campo])


def _moneda_de(encabezado: str) -> str | None:
    n = normalizar(encabezado)
    if any(m in n for m in MARCAS_USD):
        return "USD"
    if any(m in n for m in MARCAS_ARS):
        return "ARS"
    return None


def _es_columna_monto_por_moneda(encabezado: str) -> bool:
    n = normalizar(encabezado)
    if "saldo" in n or _moneda_de(n) is None:
        return False
    return (_es(n, "importe") or _es(n, "debito") or _es(n, "credito")
            or n in ("pesos", "dolares", "$", "u$s", "usd", "us$", "en pesos", "en dolares"))


def _buscar_encabezado(tabla: list[list]) -> int:
    for i, fila in enumerate(tabla[:60]):
        celdas = [str(c) for c in fila if not es_vacio(c)]
        tiene_fecha = any(_es(c, "fecha") for c in celdas)
        tiene_desc = any(_es(c, "descripcion") for c in celdas)
        tiene_monto = any(_es(c, "importe") or _es(c, "debito") or _es(c, "credito")
                          or _es_columna_monto_por_moneda(c) for c in celdas)
        if tiene_fecha and tiene_desc and tiene_monto:
            return i
    raise ErrorFormato(
        "No encontré la fila de encabezados (necesito columnas de fecha, descripción e "
        "importe). Indicá `fila_encabezado` y `columnas` para esta cuenta en config.yaml."
    )


def _mapear_columnas(encabezados: list[str], cuenta: dict) -> dict:
    manual = cuenta.get("columnas", {}) or {}

    def indice(nombre: str) -> int:
        for i, h in enumerate(encabezados):
            if normalizar(h) == normalizar(nombre):
                return i
        raise ErrorFormato(f"No existe la columna {nombre!r}. Columnas: {encabezados}")

    def buscar(campo: str) -> int | None:
        if campo in manual:
            return indice(manual[campo])
        for i, h in enumerate(encabezados):
            if _es(h, campo) and not (campo == "importe" and _moneda_de(h)):
                return i
        return None

    cols: dict = {"fecha": buscar("fecha")}
    if cols["fecha"] is None:
        raise ErrorFormato(f"No encontré la columna de fecha. Columnas: {encabezados}")

    desc = manual.get("descripcion")
    if desc:
        cols["descripcion"] = [indice(d) for d in (desc if isinstance(desc, list) else [desc])]
    else:
        i = buscar("descripcion")
        if i is None:
            raise ErrorFormato(f"No encontré la columna de descripción. Columnas: {encabezados}")
        cols["descripcion"] = [i]

    cols["moneda"] = buscar("moneda")
    cols["por_moneda"] = {}
    if cuenta.get("montos_por_moneda"):
        cols["por_moneda"] = {m: indice(c) for m, c in cuenta["montos_por_moneda"].items()}
        cols["importe"] = cols["debito"] = cols["credito"] = None
    else:
        cols["importe"] = buscar("importe")
        cols["debito"] = buscar("debito")
        cols["credito"] = buscar("credito")
        if cols["importe"] is None and cols["debito"] is None and cols["credito"] is None:
            cols["por_moneda"] = {}
            for i, h in enumerate(encabezados):
                if _es_columna_monto_por_moneda(h):
                    cols["por_moneda"].setdefault(_moneda_de(h), i)
            if not cols["por_moneda"]:
                raise ErrorFormato(f"No encontré columnas de importe. Columnas: {encabezados}")

    usadas = {cols["fecha"], cols["moneda"], cols["importe"], cols["debito"], cols["credito"],
              *cols["descripcion"], *cols["por_moneda"].values()}
    cols["usadas"] = {u for u in usadas if u is not None}
    return cols


def _montos(fila: list, cols: dict, decimal: str, moneda_default: str):
    if cols["por_moneda"]:
        for moneda, i in cols["por_moneda"].items():
            yield moneda, parse_importe(fila[i], decimal)
        return

    moneda = moneda_default
    if cols["moneda"] is not None and not es_vacio(fila[cols["moneda"]]):
        moneda = _moneda_de(str(fila[cols["moneda"]])) or str(fila[cols["moneda"]]).strip().upper()

    if cols["importe"] is not None:
        yield moneda, parse_importe(fila[cols["importe"]], decimal)
        return
    debito = parse_importe(fila[cols["debito"]], decimal) if cols["debito"] is not None else None
    credito = parse_importe(fila[cols["credito"]], decimal) if cols["credito"] is not None else None
    # el débito resta aunque el banco lo muestre en positivo
    total = (credito or 0) - abs(debito or 0)
    yield moneda, total if (debito is not None or credito is not None) else None
