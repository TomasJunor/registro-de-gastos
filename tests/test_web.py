"""Prueba el ejecutor de pasos contra un 'home banking' falso servido localmente."""
import http.server
import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from gastos.importers import web  # noqa: E402

PAGINA = """<html><body>
<div id="login">
  <input id="dni"><input id="clave" type="password">
  <button onclick="document.getElementById('login').style.display='none';
                   document.getElementById('home').style.display='block'">Ingresar</button>
</div>
<div id="home" style="display:none">
  <h1>Mis cuentas</h1>
  <a href="/movimientos.csv" download="movimientos.csv">Descargar</a>
</div>
</body></html>"""
CSV = "Fecha;Descripcion;Importe\n10/09/2026;SPOTIFY;-4.599,00\n"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        cuerpo, tipo = (CSV, "text/csv") if self.path.endswith(".csv") else (PAGINA, "text/html")
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.end_headers()
        self.wfile.write(cuerpo.encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def servidor():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def _navegador():
    ruta = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    return ruta if Path(ruta).exists() else None


def test_descarga_web_con_pasos(servidor, tmp_path, monkeypatch):
    monkeypatch.setenv("BANCO_DNI", "12345678")
    cuenta = {"id": "banco", "descarga_web": {
        "headless": True, "timeout": 10, "navegador_ejecutable": _navegador(),
        "pasos": [
            {"ir_a": servidor},
            {"completar": {"selector": "#dni", "valor": "${BANCO_DNI}"}, "solo_si_visible": "#dni"},
            {"click": "#nada", "opcional": True},
            {"click": "text=Ingresar", "solo_si_visible": "text=Ingresar"},
            {"esperar_usuario": "no debería frenar", "solo_si_visible": "text=Token"},
            {"esperar": "text=Mis cuentas"},
            {"descargar": "text=Descargar"},
        ]}}
    if not cuenta["descarga_web"]["navegador_ejecutable"]:
        del cuenta["descarga_web"]["navegador_ejecutable"]
    bajados = web.descargar(cuenta, tmp_path / "bandeja", tmp_path / "perfil", tmp_path / "logs",
                            interactivo=False)
    assert len(bajados) == 1
    assert bajados[0].read_text() == CSV


def test_error_guarda_captura(servidor, tmp_path):
    cuenta = {"id": "banco", "descarga_web": {
        "headless": True, "timeout": 2, "navegador_ejecutable": _navegador(),
        "pasos": [{"ir_a": servidor}, {"click": "#no-existe"}]}}
    if not cuenta["descarga_web"]["navegador_ejecutable"]:
        del cuenta["descarga_web"]["navegador_ejecutable"]
    with pytest.raises(web.ErrorDescarga, match="captura"):
        web.descargar(cuenta, tmp_path / "b", tmp_path / "p", tmp_path / "logs", interactivo=False)
    assert list((tmp_path / "logs").glob("banco_error_*.png"))
