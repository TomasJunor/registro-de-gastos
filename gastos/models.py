from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field

from gastos.parsing import normalizar


@dataclass
class Movimiento:
    cuenta: str                 # id de la cuenta en config.yaml (ej. "santander_pesos")
    fecha: dt.date
    descripcion: str
    monto: float                # positivo = entra plata, negativo = sale plata
    moneda: str = "ARS"
    id_externo: str | None = None   # id que da la fuente (ej. SOURCE_ID de Mercado Pago)
    origen: str = ""            # archivo o API de donde salió
    extra: dict = field(default_factory=dict)
    categoria: str | None = None

    def clave_dedup(self) -> str:
        """Clave que identifica el movimiento aunque se importe varias veces."""
        if self.id_externo:
            base = f"{self.cuenta}|ext|{self.id_externo}"
        else:
            base = "|".join([
                self.cuenta, self.fecha.isoformat(), f"{self.monto:.2f}",
                self.moneda, normalizar(self.descripcion),
            ])
        return hashlib.sha1(base.encode()).hexdigest()


def asignar_ids(movimientos: list[Movimiento]) -> list[tuple[str, Movimiento]]:
    """Devuelve (id, movimiento). Si en una misma tanda hay movimientos idénticos
    (ej. dos cafés iguales el mismo día) les agrega un número de ocurrencia para
    no perder ninguno, y el resultado es estable si se reimporta el mismo archivo."""
    vistos: dict[str, int] = {}
    salida = []
    for mov in movimientos:
        clave = mov.clave_dedup()
        n = vistos.get(clave, 0)
        vistos[clave] = n + 1
        id_ = clave if n == 0 else hashlib.sha1(f"{clave}|{n}".encode()).hexdigest()
        salida.append((id_, mov))
    return salida
