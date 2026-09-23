"""Parseo tolerante de fechas e importes en formatos argentinos."""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
    # por si el export viene en inglés
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos ni espacios repetidos. Útil para comparar encabezados."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


def es_vacio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    return str(valor).strip() in ("", "-", "nan", "NaT", "None")


def parse_fecha(valor, dayfirst: bool = True) -> dt.date | None:
    """Acepta date/datetime/Timestamp, 'dd/mm/aaaa', 'dd/mm/aa', 'aaaa-mm-dd', '12-sep-2026', etc."""
    if es_vacio(valor):
        return None
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    if hasattr(valor, "to_pydatetime"):  # pandas.Timestamp
        return valor.to_pydatetime().date()

    s = str(valor).strip()
    # ISO con hora: 2026-09-22T10:11:12.000-03:00
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))

    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", s)
    if m:
        a, b, anio = int(m[1]), int(m[2]), int(m[3])
        dia, mes = (a, b) if dayfirst else (b, a)
        if anio < 100:
            anio += 2000
        return dt.date(anio, mes, dia)

    # 12-sep-2026 / 12 Sep 26 / 12-Set-26
    m = re.match(r"^(\d{1,2})[\s\-/.]+([A-Za-zÁÉÍÓÚáéíóú]{3})[A-Za-z]*[\s\-/.]+(\d{2,4})", s)
    if m:
        mes = MESES.get(normalizar(m[2])[:3])
        if mes:
            anio = int(m[3])
            if anio < 100:
                anio += 2000
            return dt.date(anio, mes, int(m[1]))

    raise ValueError(f"No pude interpretar la fecha: {valor!r}")


def parse_importe(valor, decimal: str = ",") -> float | None:
    """Convierte '$ -1.234,56', '(1.234,56)', '1234.56', 'U$S 10,00', 1234.5 -> float.

    `decimal` indica el separador decimal esperado cuando hay ambigüedad
    (ej. '1.234' es mil doscientos treinta y cuatro en formato argentino).
    """
    if es_vacio(valor):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)

    s = str(valor).strip()
    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    if s.endswith("-"):  # formato contable '1.234,56-'
        negativo, s = True, s[:-1]
    s = re.sub(r"(?i)u\$s|usd|ars|\$", "", s).replace(" ", "").replace(" ", "")
    if s.startswith("-"):
        negativo, s = not negativo, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not s:
        return None

    if "," in s and "." in s:
        # el separador que aparece último es el decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        partes = s.split(",")
        es_miles = len(partes) > 2 or (decimal == "." and len(partes[1]) == 3)
        s = s.replace(",", "") if es_miles else s.replace(",", ".")
    elif "." in s:
        partes = s.split(".")
        # '1.234' o '1.234.567' con decimal ',' => separador de miles
        if decimal == "," and (len(partes) > 2 or len(partes[1]) == 3):
            s = s.replace(".", "")

    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ValueError(f"No pude interpretar el importe: {valor!r}")
    numero = float(s)
    return -numero if negativo else numero
