from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    raiz: Path
    db: Path
    categorias: Path
    reglas_personales: Path
    procesados: Path
    cuentas: list[dict] = field(default_factory=list)

    def cuenta(self, id_: str) -> dict:
        for c in self.cuentas:
            if c["id"] == id_:
                return c
        raise KeyError(f"No hay ninguna cuenta con id {id_!r} en config.yaml")

    def ruta(self, valor: str | Path) -> Path:
        p = Path(valor).expanduser()
        return p if p.is_absolute() else self.raiz / p


def cargar_env(ruta: Path):
    """Carga un .env simple (CLAVE=valor) sin pisar variables ya definidas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def expandir_env(texto: str) -> str:
    """Reemplaza ${VAR} por el valor de la variable de entorno."""
    def reemplazo(m):
        valor = os.environ.get(m[1])
        if valor is None:
            raise KeyError(f"Falta la variable de entorno {m[1]} (definila en .env)")
        return valor
    return re.sub(r"\$\{(\w+)\}", reemplazo, texto)


def cargar(ruta: str | Path = "config.yaml") -> Config:
    ruta = Path(ruta).resolve()
    if not ruta.exists():
        raise SystemExit(
            f"No encontré {ruta}. Copiá config.example.yaml a config.yaml y completalo."
        )
    raiz = ruta.parent
    cargar_env(raiz / ".env")
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    rutas = datos.get("rutas", {})

    def r(clave, default):
        p = Path(rutas.get(clave, default)).expanduser()
        return p if p.is_absolute() else raiz / p

    cuentas = datos.get("cuentas", [])
    ids = [c.get("id") for c in cuentas]
    if None in ids or len(ids) != len(set(ids)):
        raise SystemExit("config.yaml: cada cuenta necesita un 'id' único")
    return Config(
        raiz=raiz,
        db=r("db", "data/gastos.db"),
        categorias=r("categorias", "categorias.yaml"),
        reglas_personales=r("reglas_personales", "reglas_personales.yaml"),
        procesados=r("procesados", "data/procesados"),
        cuentas=cuentas,
    )
