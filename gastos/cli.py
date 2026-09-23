"""Línea de comandos: `gastos <comando>` (o `python -m gastos <comando>`)."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from gastos import report
from gastos.categorize import TIPOS, Categorizador, agregar_regla_personal
from gastos.config import Config, cargar
from gastos.db import DB
from gastos.importers import mercadopago, web
from gastos.importers.archivo import EXTENSIONES, importar_archivo

log = logging.getLogger("gastos")


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="gastos", description=__doc__)
    ap.add_argument("-c", "--config", default="config.yaml", help="ruta a config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("sync", help="descarga/importa movimientos de todas las cuentas")
    p.add_argument("--cuenta", action="append", help="solo esta(s) cuenta(s)")
    p.add_argument("--desde", type=_fecha, help="forzar fecha de inicio (API)")
    p.add_argument("--sin-web", action="store_true", help="no abrir el navegador; solo procesar la bandeja")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("importar", help="importa un archivo puntual (CSV/XLSX/XLS)")
    p.add_argument("archivo", nargs="+", type=Path)
    p.add_argument("--cuenta", required=True)
    p.set_defaults(func=cmd_importar)

    p = sub.add_parser("categorizar", help="asigná categoría a lo que quedó sin categoría (interactivo)")
    p.set_defaults(func=cmd_categorizar)

    p = sub.add_parser("recategorizar", help="reaplica las reglas a todo (salvo lo asignado a mano)")
    p.set_defaults(func=cmd_recategorizar)

    p = sub.add_parser("asignar", help="asigna a mano la categoría de un movimiento")
    p.add_argument("id", help="id (o prefijo) del movimiento; ver `gastos movimientos`")
    p.add_argument("categoria")
    p.set_defaults(func=cmd_asignar)

    p = sub.add_parser("resumen", help="ingresos/egresos por categoría y mes")
    _args_periodo(p)
    p.set_defaults(func=cmd_resumen)

    p = sub.add_parser("movimientos", help="lista movimientos")
    _args_periodo(p)
    p.add_argument("--cuenta")
    p.add_argument("--categoria")
    p.add_argument("--sin-categoria", action="store_true")
    p.set_defaults(func=cmd_movimientos)

    p = sub.add_parser("exportar", help="genera un Excel con movimientos y resúmenes")
    _args_periodo(p, meses_default=None)
    p.add_argument("--salida", type=Path, default=Path("data/reportes/gastos.xlsx"))
    p.set_defaults(func=cmd_exportar)

    p = sub.add_parser("mp-config", help="configura el reporte de Mercado Pago (una sola vez)")
    p.add_argument("--cuenta", default=None)
    p.set_defaults(func=cmd_mp_config)

    p = sub.add_parser("grabar", help="abre un navegador que anota los selectores de lo que clickeás")
    p.add_argument("url")
    p.set_defaults(func=cmd_grabar)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.comando == "grabar":
        return args.func(args, None)
    cfg = cargar(args.config)
    sys.exit(args.func(args, cfg) or 0)


# --- helpers ---------------------------------------------------------------------

def _fecha(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _args_periodo(p, meses_default: int | None = 3):
    p.add_argument("--desde", type=_fecha)
    p.add_argument("--hasta", type=_fecha)
    p.add_argument("--meses", type=int, default=meses_default,
                   help=f"últimos N meses (default {meses_default})" if meses_default else None)


def _periodo(args) -> tuple[dt.date | None, dt.date | None]:
    desde, hasta = args.desde, args.hasta
    if desde is None and getattr(args, "meses", None):
        hoy = dt.date.today()
        mes = hoy.month - args.meses + 1
        anio = hoy.year + (mes - 1) // 12
        desde = dt.date(anio, (mes - 1) % 12 + 1, 1)
    return desde, hasta


def _categorizador(cfg: Config) -> Categorizador:
    # las reglas personales van primero: tienen prioridad
    return Categorizador.desde_archivos(cfg.reglas_personales, cfg.categorias)


def aplicar_reglas(db: DB, cat: Categorizador, todo: bool = False) -> int:
    """Categoriza movimientos. Con todo=False solo los que no tienen categoría."""
    cambios = 0
    filas = db.con.execute(
        "SELECT id, descripcion, cuenta, monto, categoria FROM movimientos "
        "WHERE categoria_manual = 0" + ("" if todo else " AND categoria IS NULL")
    ).fetchall()
    for f in filas:
        nueva = cat.categorizar(f["descripcion"], f["cuenta"], f["monto"])
        if nueva != f["categoria"]:
            db.set_categoria(f["id"], nueva)
            cambios += 1
    return cambios


# --- comandos --------------------------------------------------------------------

def cmd_sync(args, cfg: Config) -> int:
    db = DB(cfg.db)
    cuentas = [c for c in cfg.cuentas if c.get("activa", True)]
    if args.cuenta:
        cuentas = [cfg.cuenta(c) for c in args.cuenta]
    errores = []
    for cuenta in cuentas:
        try:
            nuevos = _sync_cuenta(cfg, db, cuenta, args)
            print(f"✔ {cuenta['id']}: {nuevos} movimientos nuevos")
        except Exception as e:  # una cuenta rota no frena a las demás
            log.debug("detalle", exc_info=True)
            errores.append(cuenta["id"])
            print(f"✘ {cuenta['id']}: {e}", file=sys.stderr)

    categorizados = aplicar_reglas(db, _categorizador(cfg))
    pendientes = len(db.consultar(sin_categoria=True))
    print(f"Categorizados automáticamente: {categorizados}. Sin categoría: {pendientes}")
    db.close()
    return 1 if errores else 0


def _sync_cuenta(cfg: Config, db: DB, cuenta: dict, args) -> int:
    tipo = cuenta.get("importador", "archivo")
    if tipo == "mercadopago_api":
        hoy = dt.date.today()
        ultima = db.ultima_fecha(cuenta["id"])
        if args.desde:
            desde = args.desde
        elif ultima:
            desde = ultima - dt.timedelta(days=cuenta.get("dias_solapamiento", 5))
        else:
            desde = hoy - dt.timedelta(days=cuenta.get("dias_iniciales", 90))
        movs = mercadopago.sincronizar(cuenta, desde, hoy,
                                       carpeta_crudos=cfg.procesados / cuenta["id"])
        return db.insertar(movs)

    if tipo == "archivo":
        carpeta = cfg.ruta(cuenta.get("carpeta", f"data/bandeja/{cuenta['id']}"))
        carpeta.mkdir(parents=True, exist_ok=True)
        error_web = None
        if cuenta.get("descarga_web") and not args.sin_web:
            try:
                web.descargar(cuenta, destino=carpeta,
                              perfil=cfg.ruta(cuenta["descarga_web"].get(
                                  "perfil_navegador", f"data/navegador/{cuenta['id']}")),
                              carpeta_logs=cfg.ruta("data/logs"))
            except web.ErrorDescarga as e:
                error_web = e  # igual proceso lo que se haya dejado a mano en la bandeja
        nuevos = _procesar_bandeja(cfg, db, cuenta, carpeta)
        if error_web:
            raise RuntimeError(f"{error_web} ({nuevos} movimientos nuevos desde la bandeja)")
        return nuevos

    raise ValueError(f"importador desconocido: {tipo!r} (usá 'archivo' o 'mercadopago_api')")


def _procesar_bandeja(cfg: Config, db: DB, cuenta: dict, carpeta: Path) -> int:
    nuevos = 0
    destino = cfg.procesados / cuenta["id"]
    for archivo in sorted(carpeta.iterdir()):
        if archivo.suffix.lower() not in EXTENSIONES or archivo.name.startswith((".", "~$")):
            continue
        try:
            movs = importar_archivo(archivo, cuenta)
        except Exception as e:
            raise RuntimeError(f"no pude leer {archivo} (queda en la bandeja): {e}") from e
        n = db.insertar(movs)
        log.info("%s: %d filas, %d nuevas", archivo.name, len(movs), n)
        nuevos += n
        destino.mkdir(parents=True, exist_ok=True)
        final = destino / archivo.name
        if final.exists():
            final = destino / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{archivo.name}"
        shutil.move(str(archivo), final)
    return nuevos


def cmd_importar(args, cfg: Config) -> int:
    db = DB(cfg.db)
    cuenta = cfg.cuenta(args.cuenta)
    for archivo in args.archivo:
        movs = importar_archivo(archivo, cuenta)
        print(f"{archivo.name}: {len(movs)} movimientos leídos, {db.insertar(movs)} nuevos")
    print(f"Categorizados: {aplicar_reglas(db, _categorizador(cfg))}")
    db.close()


def cmd_recategorizar(args, cfg: Config) -> int:
    db = DB(cfg.db)
    print(f"Movimientos que cambiaron de categoría: {aplicar_reglas(db, _categorizador(cfg), todo=True)}")
    db.close()


def cmd_asignar(args, cfg: Config) -> int:
    db = DB(cfg.db)
    filas = db.con.execute("SELECT id, descripcion FROM movimientos WHERE id LIKE ?",
                           (args.id + "%",)).fetchall()
    if len(filas) != 1:
        print(f"El id {args.id!r} coincide con {len(filas)} movimientos; usá más caracteres.")
        return 1
    db.set_categoria(filas[0]["id"], args.categoria, manual=True)
    print(f"{filas[0]['descripcion']} -> {args.categoria}")
    cat = _categorizador(cfg)
    if args.categoria not in cat.tipos:
        print(f"Ojo: {args.categoria!r} no está definida en las reglas; se contará como "
              "ingreso/egreso según el signo del monto.")
    db.close()


def _clave_agrupacion(descripcion: str) -> str:
    """Agrupa descripciones que solo difieren en números (ej. nro de operación)."""
    return re.sub(r"\s+", " ", re.sub(r"[\d*#/:.\-]+", " ", descripcion)).strip().upper()


def cmd_categorizar(args, cfg: Config) -> int:
    db = DB(cfg.db)
    cat = _categorizador(cfg)
    grupos: dict[str, list] = {}
    for f in db.consultar(sin_categoria=True):
        if not f["categoria_manual"]:
            grupos.setdefault(_clave_agrupacion(f["descripcion"]), []).append(f)
    if not grupos:
        print("No hay movimientos sin categoría. 🎉")
        return 0

    nombres = sorted(cat.tipos)
    print("Categorías existentes:")
    for i, n in enumerate(nombres, 1):
        print(f"  {i:>2}. {n} ({cat.tipos[n]})")
    print("\nPara cada grupo: número de categoría, nombre nuevo, Enter para saltear, 'q' para salir.")

    ordenados = sorted(grupos.items(), key=lambda kv: -sum(abs(f["monto"]) for f in kv[1]))
    for clave, filas in ordenados:
        total = sum(f["monto"] for f in filas)
        ej = filas[0]
        print(f"\n[{len(filas)} mov, total {total:,.2f} {ej['moneda']}] {ej['descripcion']}  "
              f"(cuenta {ej['cuenta']}, {ej['fecha']})")
        resp = input("categoría> ").strip()
        if resp.lower() == "q":
            break
        if not resp:
            continue
        if resp.isdigit() and 1 <= int(resp) <= len(nombres):
            nombre, tipo = nombres[int(resp) - 1], cat.tipos[nombres[int(resp) - 1]]
        else:
            nombre = resp
            tipo = input(f"tipo ({'/'.join(TIPOS)}) [{'ingreso' if total > 0 else 'egreso'}]> ").strip()
            tipo = tipo or ("ingreso" if total > 0 else "egreso")
            if tipo not in TIPOS:
                print("Tipo inválido, salteo.")
                continue
            nombres.append(nombre)
        sugerido = re.escape(clave.split(" - ")[0]).replace("\\ ", " ")[:60]
        patron = input(f"patrón (regex) [{sugerido}]> ").strip() or sugerido
        agregar_regla_personal(cfg.reglas_personales, nombre, tipo, patron)
        cat = _categorizador(cfg)
        print(f"  -> {aplicar_reglas(db, cat)} movimientos categorizados como {nombre}")
    db.close()


def cmd_resumen(args, cfg: Config) -> int:
    db = DB(cfg.db)
    desde, hasta = _periodo(args)
    print(report.texto_resumen(report.dataframe(db, _categorizador(cfg), desde, hasta)))
    db.close()


def cmd_movimientos(args, cfg: Config) -> int:
    db = DB(cfg.db)
    desde, hasta = _periodo(args)
    filas = db.consultar(desde, hasta, cuenta=args.cuenta, categoria=args.categoria,
                         sin_categoria=args.sin_categoria)
    for f in filas:
        print(f"{f['id'][:8]}  {f['fecha']}  {f['cuenta']:<18.18} {f['monto']:>14,.2f} {f['moneda']}  "
              f"{(f['categoria'] or '-'):<22.22} {f['descripcion']}")
    print(f"{len(filas)} movimientos")
    db.close()


def cmd_exportar(args, cfg: Config) -> int:
    db = DB(cfg.db)
    desde, hasta = _periodo(args)
    df = report.dataframe(db, _categorizador(cfg), desde, hasta)
    if df.empty:
        print("No hay movimientos para exportar.")
        return 1
    salida = args.salida if args.salida.is_absolute() else cfg.raiz / args.salida
    print(f"Excel generado: {report.exportar_excel(df, salida)}")
    db.close()


def cmd_mp_config(args, cfg: Config) -> int:
    import os
    cuentas = [c for c in cfg.cuentas if c.get("importador") == "mercadopago_api"]
    if args.cuenta:
        cuentas = [cfg.cuenta(args.cuenta)]
    if not cuentas:
        print("No hay cuentas con importador: mercadopago_api en config.yaml")
        return 1
    for c in cuentas:
        token = os.environ.get(c.get("token_env", "MP_ACCESS_TOKEN"))
        if not token:
            print(f"Falta {c.get('token_env', 'MP_ACCESS_TOKEN')} en .env")
            return 1
        print(mercadopago.MercadoPagoAPI(token).configurar_reporte())


def cmd_grabar(args, cfg) -> int:
    print("Se abre un navegador: navegá hasta la descarga del extracto. En la ventana del "
          "inspector vas a ver los selectores para copiar a config.yaml.")
    return subprocess.call([sys.executable, "-m", "playwright", "codegen", "--lang", "es-AR",
                            "--target", "python", args.url])
