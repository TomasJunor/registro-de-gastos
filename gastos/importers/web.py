"""Descarga automática de extractos desde el home banking con Playwright.

Santander Argentina no tiene API para personas, así que la única forma de
automatizarlo es "manejar" el navegador como lo harías vos. Los pasos se
describen en config.yaml (no están fijos en el código) porque el sitio del banco
cambia seguido y porque cada uno tiene cuentas/menús distintos.

Para encontrar los selectores correctos corré `gastos grabar <cuenta>`: abre un
navegador que va anotando los selectores de todo lo que clickeás.

El navegador usa un perfil persistente (cookies, "recordar dispositivo"), así
el banco suele pedir el token/2FA solo la primera vez o cada tanto. Cuando lo
pide, el paso `esperar_usuario` frena hasta que lo completes a mano.

Pasos soportados (cada uno es un item de la lista `pasos`):
    - ir_a: URL
    - click: SELECTOR
    - completar: {selector: SELECTOR, valor: "texto o ${VARIABLE_DE_ENTORNO}"}
    - seleccionar: {selector: SELECTOR, valor: "opción"}
    - presionar: {selector: SELECTOR, tecla: "Enter"}
    - esperar: SELECTOR                 (espera a que aparezca)
    - esperar_segundos: N
    - esperar_usuario: "mensaje"        (pausa hasta que apretes Enter en la terminal)
    - descargar: SELECTOR               (click que dispara la descarga; guarda el archivo)
Modificadores opcionales en cualquier paso:
    solo_si_visible: SELECTOR   -> el paso se saltea si ese elemento no está (ej. login ya hecho)
    opcional: true              -> si falla, sigue igual
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
import time
from pathlib import Path

from gastos.config import expandir_env

log = logging.getLogger(__name__)

ACCIONES = ("ir_a", "click", "completar", "seleccionar", "presionar", "esperar",
            "esperar_segundos", "esperar_usuario", "descargar")


class ErrorDescarga(RuntimeError):
    pass


def descargar(cuenta: dict, destino: Path, perfil: Path, carpeta_logs: Path,
              interactivo: bool | None = None) -> list[Path]:
    """Ejecuta los pasos de `cuenta['descarga_web']` y devuelve los archivos bajados."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ErrorDescarga(
            "Falta Playwright: pip install 'registro-de-gastos[web]' && playwright install chromium"
        ) from e

    conf = cuenta["descarga_web"]
    pasos = conf.get("pasos") or []
    if not pasos:
        raise ErrorDescarga(f"La cuenta {cuenta['id']} no tiene pasos en descarga_web")
    if interactivo is None:
        interactivo = sys.stdin.isatty()
    destino.mkdir(parents=True, exist_ok=True)
    perfil.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(conf.get("timeout", 30)) * 1000
    bajados: list[Path] = []

    with sync_playwright() as p:
        opciones = dict(headless=conf.get("headless", False), accept_downloads=True,
                        locale="es-AR", timezone_id="America/Argentina/Buenos_Aires")
        if conf.get("navegador_ejecutable"):
            opciones["executable_path"] = conf["navegador_ejecutable"]
        ctx = p.chromium.launch_persistent_context(str(perfil), **opciones)
        ctx.set_default_timeout(timeout_ms)
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for n, paso in enumerate(pasos, 1):
                _ejecutar(pagina, paso, n, cuenta, destino, bajados, interactivo)
        except Exception as e:
            carpeta_logs.mkdir(parents=True, exist_ok=True)
            captura = carpeta_logs / f"{cuenta['id']}_error_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
            try:
                pagina.screenshot(path=str(captura), full_page=True)
            except Exception:
                captura = None
            extra = f" (captura: {captura})" if captura else ""
            raise ErrorDescarga(f"{cuenta['id']}: falló la descarga web: {e}{extra}") from e
        finally:
            ctx.close()
    return bajados


def _ejecutar(pagina, paso: dict, n: int, cuenta: dict, destino: Path,
              bajados: list[Path], interactivo: bool):
    if not isinstance(paso, dict):
        raise ErrorDescarga(f"Paso {n}: formato inválido ({paso!r})")
    acciones = [a for a in ACCIONES if a in paso]
    if len(acciones) != 1:
        raise ErrorDescarga(f"Paso {n}: tiene que tener exactamente una acción de {ACCIONES}")
    accion, arg = acciones[0], paso[acciones[0]]

    condicion = paso.get("solo_si_visible")
    if condicion and not _visible(pagina, condicion):
        log.info("Paso %d (%s): salteado, no está visible %s", n, accion, condicion)
        return
    log.info("Paso %d: %s", n, accion)
    try:
        if accion == "ir_a":
            pagina.goto(expandir_env(str(arg)))
        elif accion == "click":
            pagina.locator(arg).first.click()
        elif accion == "completar":
            pagina.locator(arg["selector"]).first.fill(expandir_env(str(arg["valor"])))
        elif accion == "seleccionar":
            pagina.locator(arg["selector"]).first.select_option(expandir_env(str(arg["valor"])))
        elif accion == "presionar":
            pagina.locator(arg["selector"]).first.press(arg.get("tecla", "Enter"))
        elif accion == "esperar":
            pagina.locator(arg).first.wait_for(state="visible")
        elif accion == "esperar_segundos":
            time.sleep(float(arg))
        elif accion == "esperar_usuario":
            if not interactivo:
                raise ErrorDescarga(
                    f"el banco necesita intervención manual ({arg}). Corré "
                    f"`gastos sync --cuenta {cuenta['id']}` desde una terminal para completarla."
                )
            input(f"\n>>> {arg}\n    (presioná Enter para continuar) ")
        elif accion == "descargar":
            with pagina.expect_download() as info:
                pagina.locator(arg).first.click()
            descarga = info.value
            nombre = descarga.suggested_filename or "extracto"
            ruta = destino / f"{cuenta['id']}_{dt.datetime.now():%Y%m%d_%H%M%S}_{nombre}"
            descarga.save_as(str(ruta))
            bajados.append(ruta)
            log.info("Descargado %s", ruta)
    except Exception:
        if paso.get("opcional"):
            log.warning("Paso %d (%s) falló pero es opcional; sigo", n, accion)
            return
        raise


def _visible(pagina, selector: str, espera_ms: int = 3000) -> bool:
    try:
        pagina.locator(selector).first.wait_for(state="visible", timeout=espera_ms)
        return True
    except Exception:
        return False
