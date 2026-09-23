"""Resúmenes de ingresos/egresos por categoría y exportación a Excel."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from gastos.categorize import SIN_CATEGORIA, Categorizador
from gastos.db import DB


def dataframe(db: DB, cat: Categorizador, desde: dt.date | None = None,
              hasta: dt.date | None = None) -> pd.DataFrame:
    filas = db.consultar(desde=desde, hasta=hasta)
    columnas = ["fecha", "mes", "cuenta", "descripcion", "categoria", "tipo", "moneda", "monto",
                "id"]
    if not filas:
        return pd.DataFrame(columns=columnas)
    df = pd.DataFrame([dict(f) for f in filas])
    df["tipo"] = [cat.tipo(c, m) for c, m in zip(df["categoria"], df["monto"])]
    df["categoria"] = df["categoria"].fillna(SIN_CATEGORIA)
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["mes"] = df["fecha"].dt.strftime("%Y-%m")
    return df[columnas]


def resumen_mensual(df: pd.DataFrame, moneda: str = "ARS") -> pd.DataFrame:
    """Tabla categoría x mes. Ingresos en positivo, egresos en positivo (lo gastado)."""
    d = df[(df["moneda"] == moneda) & (df["tipo"] != "interno")].copy()
    if d.empty:
        return pd.DataFrame()
    d["importe"] = d["monto"].where(d["tipo"] == "ingreso", -d["monto"])
    bloques = []
    for tipo, titulo in (("ingreso", "Ingresos"), ("egreso", "Egresos")):
        sub = d[d["tipo"] == tipo]
        if sub.empty:
            continue
        t = sub.pivot_table(index="categoria", columns="mes", values="importe",
                            aggfunc="sum", fill_value=0.0)
        t = t.loc[t.sum(axis=1).sort_values(ascending=False).index]  # más grande primero
        t.index = pd.MultiIndex.from_product([[titulo], t.index])
        bloques.append(t)
    tabla = pd.concat(bloques).fillna(0.0)
    niveles = tabla.index.get_level_values(0)
    ingresos = tabla[niveles == "Ingresos"].sum()
    egresos = tabla[niveles == "Egresos"].sum()
    totales = pd.DataFrame(
        [ingresos, egresos, ingresos - egresos],
        index=pd.MultiIndex.from_product([["TOTAL"], ["Ingresos", "Egresos", "Balance (ahorro)"]]),
    )
    return pd.concat([tabla, totales]).round(2)


def texto_resumen(df: pd.DataFrame) -> str:
    if df.empty:
        return "No hay movimientos en el período."
    partes = []
    for moneda in sorted(df["moneda"].unique()):
        tabla = resumen_mensual(df, moneda)
        if tabla.empty:
            continue
        partes.append(f"\n=== {moneda} ===")
        partes.append(tabla.to_string(float_format=lambda x: f"{x:,.2f}"))
    internos = df[df["tipo"] == "interno"]
    if not internos.empty:
        partes.append(f"\n({len(internos)} movimientos internos excluidos: pagos de tarjeta, "
                      "transferencias entre cuentas propias, etc.)")
    sin_cat = df[df["categoria"] == SIN_CATEGORIA]
    if not sin_cat.empty:
        partes.append(f"({len(sin_cat)} movimientos sin categoría: corré `gastos categorizar`)")
    return "\n".join(partes)


def exportar_excel(df: pd.DataFrame, ruta: str | Path) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    movs = df.drop(columns=["id"]).copy()
    movs["fecha"] = movs["fecha"].dt.date
    with pd.ExcelWriter(ruta, engine="openpyxl") as xl:
        for moneda in sorted(df["moneda"].unique()):
            tabla = resumen_mensual(df, moneda)
            if not tabla.empty:
                tabla.to_excel(xl, sheet_name=f"Resumen {moneda}")
        movs.to_excel(xl, sheet_name="Movimientos", index=False)
        por_cuenta = df.pivot_table(index=["cuenta", "moneda"], columns="mes", values="monto",
                                    aggfunc="sum", fill_value=0.0).round(2)
        if not por_cuenta.empty:
            por_cuenta.to_excel(xl, sheet_name="Neto por cuenta")
        for hoja in xl.book.worksheets:
            for col in hoja.columns:
                ancho = max(len(str(c.value or "")) for c in col[:200])
                hoja.column_dimensions[col[0].column_letter].width = min(max(ancho + 2, 10), 60)
                for c in col:
                    if isinstance(c.value, float):
                        c.number_format = "#,##0.00"
    return ruta
