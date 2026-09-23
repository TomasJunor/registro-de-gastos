"""Almacenamiento en SQLite (un único archivo, fácil de respaldar)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from gastos.models import Movimiento, asignar_ids

ESQUEMA = """
CREATE TABLE IF NOT EXISTS movimientos (
    id               TEXT PRIMARY KEY,
    cuenta           TEXT NOT NULL,
    fecha            TEXT NOT NULL,
    descripcion      TEXT NOT NULL,
    monto            REAL NOT NULL,
    moneda           TEXT NOT NULL DEFAULT 'ARS',
    categoria        TEXT,
    categoria_manual INTEGER NOT NULL DEFAULT 0,
    id_externo       TEXT,
    origen           TEXT,
    extra            TEXT,
    importado_en     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mov_fecha ON movimientos(fecha);
CREATE INDEX IF NOT EXISTS idx_mov_cuenta ON movimientos(cuenta);

CREATE TABLE IF NOT EXISTS estado (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""


class DB:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.ruta)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(ESQUEMA)

    def close(self):
        self.con.close()

    # --- movimientos -------------------------------------------------------
    def insertar(self, movimientos: list[Movimiento]) -> int:
        """Inserta ignorando duplicados. Devuelve cuántos eran nuevos."""
        ahora = dt.datetime.now().isoformat(timespec="seconds")
        nuevos = 0
        with self.con:
            for id_, m in asignar_ids(movimientos):
                cur = self.con.execute(
                    """INSERT OR IGNORE INTO movimientos
                       (id, cuenta, fecha, descripcion, monto, moneda, categoria,
                        id_externo, origen, extra, importado_en)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (id_, m.cuenta, m.fecha.isoformat(), m.descripcion, round(m.monto, 2),
                     m.moneda, m.categoria, m.id_externo, m.origen,
                     json.dumps(m.extra, ensure_ascii=False, default=str) if m.extra else None,
                     ahora),
                )
                nuevos += cur.rowcount
        return nuevos

    def consultar(self, desde: dt.date | None = None, hasta: dt.date | None = None,
                  cuenta: str | None = None, categoria: str | None = None,
                  sin_categoria: bool = False) -> list[sqlite3.Row]:
        sql, params = "SELECT * FROM movimientos WHERE 1=1", []
        if desde:
            sql += " AND fecha >= ?"
            params.append(desde.isoformat())
        if hasta:
            sql += " AND fecha <= ?"
            params.append(hasta.isoformat())
        if cuenta:
            sql += " AND cuenta = ?"
            params.append(cuenta)
        if categoria:
            sql += " AND categoria = ?"
            params.append(categoria)
        if sin_categoria:
            sql += " AND categoria IS NULL"
        sql += " ORDER BY fecha, cuenta, descripcion"
        return self.con.execute(sql, params).fetchall()

    def set_categoria(self, id_: str, categoria: str | None, manual: bool = False):
        with self.con:
            self.con.execute(
                "UPDATE movimientos SET categoria = ?, categoria_manual = ? WHERE id = ?",
                (categoria, int(manual), id_),
            )

    def ultima_fecha(self, cuenta: str) -> dt.date | None:
        fila = self.con.execute(
            "SELECT MAX(fecha) FROM movimientos WHERE cuenta = ?", (cuenta,)
        ).fetchone()
        return dt.date.fromisoformat(fila[0]) if fila and fila[0] else None

    # --- estado (cursor de sincronización, etc.) --------------------------
    def get_estado(self, clave: str, default=None):
        fila = self.con.execute("SELECT valor FROM estado WHERE clave = ?", (clave,)).fetchone()
        return json.loads(fila[0]) if fila else default

    def set_estado(self, clave: str, valor):
        with self.con:
            self.con.execute(
                "INSERT INTO estado(clave, valor) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (clave, json.dumps(valor, default=str)),
            )
