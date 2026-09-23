"""Mercado Pago vía API oficial (reporte de "Dinero liberado" / release report).

El reporte trae todos los movimientos que impactan en el saldo disponible de la
cuenta: pagos con dinero en cuenta, transferencias enviadas/recibidas, cobros,
rendimientos, retiros, etc.

Necesitás un Access Token de producción: https://www.mercadopago.com.ar/developers/panel/app
(creá una aplicación -> Credenciales de producción -> Access Token). Es de solo
lectura en la práctica para este script, pero tratalo como una contraseña.

Docs: https://www.mercadopago.com.ar/developers/es/docs/reports/released-money/api
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import os
import time
from pathlib import Path

import requests

from gastos.models import Movimiento
from gastos.parsing import es_vacio, parse_fecha, parse_importe

API = "https://api.mercadopago.com"
log = logging.getLogger(__name__)

COLUMNAS = [
    "DATE", "SOURCE_ID", "EXTERNAL_REFERENCE", "RECORD_TYPE", "DESCRIPTION",
    "NET_CREDIT_AMOUNT", "NET_DEBIT_AMOUNT", "GROSS_AMOUNT", "CURRENCY",
    "PAYMENT_METHOD", "PAYMENT_METHOD_TYPE", "PAYER_NAME", "POS_NAME", "STORE_NAME",
    "TRANSACTION_DATE", "BALANCE_AMOUNT",
]

# Traducción de los códigos de DESCRIPTION a algo legible (y fácil de categorizar)
DESCRIPCIONES = {
    "payment": "Pago",
    "refund": "Devolución",
    "payout": "Retiro a cuenta bancaria",
    "asset_management_gain": "Rendimientos",
    "asset_management_loss": "Rendimientos (pérdida)",
    "credit_payment": "Pago de crédito Mercado Pago",
    "chargeback": "Contracargo",
    "mediation": "Reclamo",
    "shipping": "Envío",
    "tax_credit_debit": "Impuesto débitos y créditos",
    "tax_iva": "IVA",
}


class MercadoPagoAPI:
    def __init__(self, token: str, timeout: int = 30):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    def _req(self, metodo: str, ruta: str, **kw) -> requests.Response:
        r = self.s.request(metodo, API + ruta, timeout=self.timeout, **kw)
        if r.status_code == 401:
            raise RuntimeError("Mercado Pago rechazó el Access Token (401). Revisá MP_ACCESS_TOKEN.")
        return r

    def configurar_reporte(self):
        """Crea o actualiza la configuración del reporte con las columnas que usamos."""
        cuerpo = {
            "file_name_prefix": "registro-gastos",
            "columns": [{"key": k} for k in COLUMNAS],
            "separator": ",",
            "display_timezone": "GMT-03",
            "report_translation": "en",
            "include_withdrawal_at_end": False,
            # obligatorios para la API aunque no usemos retiros ni programación
            "execute_after_withdrawal": False,
            "check_available_balance": True,
            "compensate_detail": True,
            # no se usa (no activamos la programación), pero es obligatorio;
            # la API rechaza "daily" con cualquier value
            "frequency": {"hour": 0, "type": "monthly", "value": 1},
        }
        existe = self._req("GET", "/v1/account/release_report/config")
        metodo = "PUT" if existe.status_code == 200 else "POST"
        r = self._req(metodo, "/v1/account/release_report/config", json=cuerpo)
        r.raise_for_status()
        return r.json()

    def generar_reporte(self, desde: dt.datetime, hasta: dt.datetime,
                        espera_max: int = 600) -> str:
        """Pide un reporte para el rango y espera a que esté listo. Devuelve el CSV."""
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        cuerpo = {"begin_date": desde.strftime(fmt), "end_date": hasta.strftime(fmt)}
        antes = {x.get("file_name") for x in self.listar_reportes()}
        r = self._req("POST", "/v1/account/release_report", json=cuerpo)
        if r.status_code == 404 or r.status_code == 400 and "config" in r.text.lower():
            log.info("No había configuración de reporte; la creo.")
            self.configurar_reporte()
            r = self._req("POST", "/v1/account/release_report", json=cuerpo)
        if r.status_code not in (200, 201, 202):
            raise RuntimeError(f"Mercado Pago no generó el reporte ({r.status_code}): {r.text[:300]}")

        limite = time.monotonic() + espera_max
        pausa = 5
        while time.monotonic() < limite:
            time.sleep(pausa)
            pausa = min(pausa * 2, 30)
            for rep in self.listar_reportes():
                nombre = rep.get("file_name")
                if not nombre or nombre in antes or not nombre.endswith(".csv"):
                    continue
                if rep.get("status") not in (None, "processed", "enabled", "ready"):
                    continue
                return self.descargar(nombre)
        raise TimeoutError("El reporte de Mercado Pago no estuvo listo a tiempo; reintentá más tarde.")

    def listar_reportes(self) -> list[dict]:
        r = self._req("GET", "/v1/account/release_report/list")
        r.raise_for_status()
        datos = r.json()
        return datos if isinstance(datos, list) else datos.get("results", [])

    def descargar(self, nombre: str) -> str:
        r = self._req("GET", f"/v1/account/release_report/{nombre}")
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        return r.text

    def detalle_pago(self, id_: str) -> dict | None:
        """Datos extra de un pago (descripción del ítem, comercio). Puede no estar disponible."""
        r = self._req("GET", f"/v1/payments/{id_}")
        return r.json() if r.status_code == 200 else None


def parsear_reporte(texto: str, cuenta_id: str, moneda_default: str = "ARS",
                    detalles: dict[str, dict] | None = None) -> list[Movimiento]:
    """Convierte el CSV del release report en movimientos."""
    detalles = detalles or {}
    lector = csv.DictReader(io.StringIO(texto.lstrip("\ufeff")), delimiter=_separador(texto))
    movimientos = []
    for fila in lector:
        fila = {(k or "").strip().upper(): (v or "").strip() for k, v in fila.items()}
        if fila.get("RECORD_TYPE", "release").lower() != "release":
            continue  # saldos iniciales, totales, etc.
        credito = parse_importe(fila.get("NET_CREDIT_AMOUNT"), decimal=".") or 0.0
        debito = parse_importe(fila.get("NET_DEBIT_AMOUNT"), decimal=".") or 0.0
        monto = credito - abs(debito)
        if abs(monto) < 0.005:
            continue
        fecha = parse_fecha(fila.get("DATE") or fila.get("TRANSACTION_DATE"))
        source_id = fila.get("SOURCE_ID") or None
        codigo = fila.get("DESCRIPTION", "")
        movimientos.append(Movimiento(
            cuenta=cuenta_id,
            fecha=fecha,
            descripcion=_describir(fila, detalles.get(source_id or "")),
            monto=monto,
            moneda=fila.get("CURRENCY") or moneda_default,
            # un mismo SOURCE_ID puede tener el pago y sus impuestos/devolución
            id_externo=f"{source_id}:{codigo}:{monto:.2f}" if source_id else None,
            origen="mercadopago_api",
            extra={k: v for k, v in fila.items() if not es_vacio(v)},
        ))
    return movimientos


def _separador(texto: str) -> str:
    primera = texto.lstrip("\ufeff").split("\n", 1)[0]
    return ";" if primera.count(";") > primera.count(",") else ","


def _describir(fila: dict, detalle: dict | None) -> str:
    codigo = fila.get("DESCRIPTION", "")
    partes = [DESCRIPCIONES.get(codigo, codigo or "Movimiento")]
    for k in ("POS_NAME", "STORE_NAME", "PAYER_NAME"):
        if fila.get(k):
            partes.append(fila[k])
    if detalle:
        for k in ("description", "statement_descriptor"):
            if detalle.get(k):
                partes.append(str(detalle[k]))
    metodo = fila.get("PAYMENT_METHOD_TYPE") or fila.get("PAYMENT_METHOD")
    if metodo:
        partes.append(f"[{metodo}]")
    # sin duplicados, respetando el orden
    return " - ".join(dict.fromkeys(p for p in partes if p))


def sincronizar(cuenta: dict, desde: dt.date, hasta: dt.date,
                carpeta_crudos: Path | None = None) -> list[Movimiento]:
    token_env = cuenta.get("token_env", "MP_ACCESS_TOKEN")
    token = os.environ.get(token_env)
    if not token:
        raise RuntimeError(f"Falta {token_env} (Access Token de Mercado Pago) en .env")
    api = MercadoPagoAPI(token)
    # el reporte trabaja en UTC; Argentina es UTC-3
    inicio = dt.datetime.combine(desde, dt.time()) + dt.timedelta(hours=3)
    fin = dt.datetime.combine(hasta + dt.timedelta(days=1), dt.time()) + dt.timedelta(hours=3)
    ahora_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    fin = min(fin, ahora_utc - dt.timedelta(minutes=5))
    texto = api.generar_reporte(inicio, fin, espera_max=cuenta.get("espera_reporte", 600))
    if carpeta_crudos:  # copia del CSV original, por si hay que auditar o reprocesar
        carpeta_crudos.mkdir(parents=True, exist_ok=True)
        nombre = f"{cuenta['id']}_{desde.isoformat()}_{hasta.isoformat()}.csv"
        (carpeta_crudos / nombre).write_text(texto, encoding="utf-8")

    detalles: dict[str, dict] = {}
    if cuenta.get("enriquecer", True):
        ids = {f.get("SOURCE_ID") for f in csv.DictReader(io.StringIO(texto.lstrip("\ufeff")),
                                                          delimiter=_separador(texto))}
        for id_ in filter(None, ids):
            try:
                d = api.detalle_pago(id_)
            except requests.RequestException as e:
                log.debug("No pude traer el detalle del pago %s: %s", id_, e)
                continue
            if d:
                detalles[id_] = {k: d.get(k) for k in ("description", "statement_descriptor")}
    return parsear_reporte(texto, cuenta["id"], cuenta.get("moneda", "ARS"), detalles)

