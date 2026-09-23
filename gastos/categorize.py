"""Categorización por reglas (expresiones regulares sobre la descripción).

Las categorías se definen en `categorias.yaml` (versionado, reglas generales) y
`reglas_personales.yaml` (tuyo, no se sube al repo; tiene prioridad). Cada
categoría tiene un `tipo`:

- ingreso:  suma a ingresos (sueldo, rendimientos, ventas...)
- egreso:   suma a egresos (supermercado, servicios...). Un reintegro dentro de
            una categoría de egreso resta del gasto.
- interno:  movimientos entre tus propias cuentas (pago de la tarjeta,
            transferencia Santander -> Mercado Pago, compra de dólares...).
            No cuentan como ingreso ni egreso, así no se cuenta dos veces.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TIPOS = ("ingreso", "egreso", "interno")
SIN_CATEGORIA = "Sin categoría"


@dataclass
class Regla:
    patron: re.Pattern
    cuentas: set[str] = field(default_factory=set)   # vacío = todas
    signo: str | None = None                          # "+", "-" o None

    def aplica(self, descripcion: str, cuenta: str, monto: float) -> bool:
        if self.cuentas and cuenta not in self.cuentas:
            return False
        if self.signo == "+" and monto < 0 or self.signo == "-" and monto > 0:
            return False
        return bool(self.patron.search(descripcion))


@dataclass
class Categoria:
    nombre: str
    tipo: str
    reglas: list[Regla]


class Categorizador:
    def __init__(self, categorias: list[Categoria]):
        self.categorias = categorias
        self.tipos = {c.nombre: c.tipo for c in categorias}

    @classmethod
    def desde_archivos(cls, *rutas: str | Path) -> "Categorizador":
        """El primer archivo tiene prioridad sobre los siguientes."""
        categorias: list[Categoria] = []
        for ruta in rutas:
            ruta = Path(ruta)
            if not ruta.exists():
                continue
            datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
            for c in datos.get("categorias", []):
                categorias.append(_parse_categoria(c, ruta))
        return cls(categorias)

    def categorizar(self, descripcion: str, cuenta: str, monto: float) -> str | None:
        for cat in self.categorias:
            if any(r.aplica(descripcion, cuenta, monto) for r in cat.reglas):
                return cat.nombre
        return None

    def tipo(self, categoria: str | None, monto: float) -> str:
        """Tipo contable de un movimiento. Sin categoría => se decide por el signo."""
        if categoria and categoria in self.tipos:
            return self.tipos[categoria]
        return "ingreso" if monto > 0 else "egreso"


def _parse_categoria(c: dict, origen: Path) -> Categoria:
    nombre = c.get("nombre")
    tipo = c.get("tipo", "egreso")
    if not nombre:
        raise ValueError(f"{origen}: hay una categoría sin 'nombre'")
    if tipo not in TIPOS:
        raise ValueError(f"{origen}: la categoría {nombre!r} tiene tipo {tipo!r}; usá uno de {TIPOS}")
    cuentas = set(_lista(c.get("cuentas")))
    signo = c.get("signo")
    reglas = []
    for p in _lista(c.get("patrones")):
        if isinstance(p, dict):  # regla con condiciones propias
            reglas.append(Regla(
                re.compile(p["patron"], re.IGNORECASE),
                set(_lista(p.get("cuentas"))) or cuentas,
                p.get("signo", signo),
            ))
        else:
            reglas.append(Regla(re.compile(str(p), re.IGNORECASE), cuentas, signo))
    return Categoria(nombre, tipo, reglas)


def _lista(valor) -> list:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def agregar_regla_personal(ruta: str | Path, categoria: str, tipo: str, patron: str):
    """Agrega un patrón a reglas_personales.yaml (creando la categoría si hace falta)."""
    ruta = Path(ruta)
    datos = {}
    if ruta.exists():
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    categorias = datos.setdefault("categorias", [])
    for c in categorias:
        if c.get("nombre") == categoria:
            patrones = _lista(c.get("patrones"))
            if patron not in patrones:
                patrones.append(patron)
            c["patrones"] = patrones
            break
    else:
        categorias.append({"nombre": categoria, "tipo": tipo, "patrones": [patron]})
    ruta.write_text(
        "# Reglas aprendidas con `gastos categorizar`. Tienen prioridad sobre categorias.yaml.\n"
        + yaml.safe_dump(datos, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
