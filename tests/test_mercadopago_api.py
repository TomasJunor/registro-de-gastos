"""Flujo de la API de Mercado Pago con respuestas simuladas (no llama a internet)."""
import datetime as dt
import json
from pathlib import Path

from gastos.importers import mercadopago

CSV = (Path(__file__).parent / "fixtures" / "mercadopago_release.csv").read_text()


class Resp:
    def __init__(self, status, cuerpo=""):
        self.status_code = status
        self.text = cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
        self.encoding = "utf-8"

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeMP:
    """Imita la API: sin config la primera vez, y el reporte aparece al 2do listado."""

    def __init__(self):
        self.llamadas = []
        self.config = None
        self.listados = 0

    def __call__(self, metodo, url, **kw):
        ruta = url.replace(mercadopago.API, "")
        self.llamadas.append((metodo, ruta, kw.get("json")))
        if ruta == "/v1/account/release_report/config":
            if metodo == "GET":
                return Resp(200, self.config) if self.config else Resp(404, {"message": "not found"})
            self.config = kw["json"]
            return Resp(201, self.config)
        if ruta == "/v1/account/release_report" and metodo == "POST":
            return Resp(202, {}) if self.config else Resp(404, {"message": "config not found"})
        if ruta == "/v1/account/release_report/list":
            self.listados += 1
            viejo = [{"file_name": "viejo.csv", "status": "processed"}]
            nuevo = [{"file_name": "registro-gastos-1.csv", "status": "processed"}]
            return Resp(200, viejo + (nuevo if self.listados >= 3 else []))
        if ruta == "/v1/account/release_report/registro-gastos-1.csv":
            return Resp(200, CSV)
        if ruta.startswith("/v1/payments/"):
            return Resp(200, {"description": "Netflix"}) if ruta.endswith("444") else Resp(404, {})
        return Resp(500, "inesperado")


def test_sincronizar(monkeypatch, tmp_path):
    fake = FakeMP()
    monkeypatch.setenv("MP_ACCESS_TOKEN", "APP_USR-test")
    monkeypatch.setattr(mercadopago.requests.Session, "request", lambda self, m, u, **kw: fake(m, u, **kw))
    monkeypatch.setattr(mercadopago.time, "sleep", lambda s: None)

    movs = mercadopago.sincronizar({"id": "mp"}, dt.date(2026, 9, 1), dt.date(2026, 9, 6),
                                   carpeta_crudos=tmp_path)
    assert [m.monto for m in movs] == [100000.0, -8500.0, 1234.56, -20000.0]
    assert movs[3].descripcion == "Pago - Netflix - [account_money]"
    # creó la config al no existir y pidió el rango en UTC (Argentina = UTC-3)
    posts = [c for c in fake.llamadas if c[:2] == ("POST", "/v1/account/release_report")]
    assert posts[-1][2] == {"begin_date": "2026-09-01T03:00:00Z", "end_date": "2026-09-07T03:00:00Z"}
    assert fake.config["report_translation"] == "en"
    assert (tmp_path / "mp_2026-09-01_2026-09-06.csv").read_text() == CSV
