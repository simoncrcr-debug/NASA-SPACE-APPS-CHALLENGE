#!/usr/bin/env python3
"""Servidor local para Cielo Claro: sirve la página y conecta con IRSA SIA2."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from pathlib import Path
import json
import posixpath
import shutil
import tempfile
import webbrowser


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
MAX_FITS_BYTES = 512 * 1024 * 1024
IRSA_HOST = "irsa.ipac.caltech.edu"
FITS_PATH_PREFIX = "/ibe/data/spherex/"


def is_official_fits_url(url):
    """True only for https URLs on IRSA under the SPHEREx data path (no dot-segment escapes)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname != IRSA_HOST or parsed.port not in (None, 443):
        return False
    normalized = posixpath.normpath(parsed.path)
    return normalized.startswith(FITS_PATH_PREFIX) and normalized == parsed.path.rstrip("/")


class OfficialFitsRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only while they stay on the official IRSA SPHEREx data path."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_official_fits_url(newurl):
            raise HTTPError(newurl, code, "Redirección fuera de IRSA/SPHEREx bloqueada.", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


FITS_OPENER = build_opener(OfficialFitsRedirectHandler)


class FitsTooLarge(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = "CieloClaro/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/sia":
            self.handle_sia(parse_qs(parsed.query))
        elif parsed.path == "/api/fits":
            self.handle_fits(parse_qs(parsed.query))
        elif parsed.path == "/api/object":
            self.handle_object(parse_qs(parsed.query))
        elif parsed.path == "/api/name":
            self.handle_name(parse_qs(parsed.query))
        elif parsed.path == "/api/health":
            self.send_json(200, {"ok": True, "service": "Cielo Claro", "irsa": IRSA_HOST})
        else:
            self.serve_page(parsed.path)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_sia(self, query):
        try:
            ra = float(query.get("ra", [""])[0])
            dec = float(query.get("dec", [""])[0])
            radius = float(query.get("radius", ["0.05"])[0])
            if not (0 <= ra <= 360 and -90 <= dec <= 90 and 0 < radius <= 1):
                raise ValueError("Coordenadas o radio fuera de rango.")
        except (TypeError, ValueError) as exc:
            self.send_json(400, {"error": str(exc) or "Coordenadas no válidas."})
            return

        params = [
            ("COLLECTION", "spherex_dr1_cube"),
            ("COLLECTION", "spherex_dr1"),
            ("COLLECTION", "spherex_qr3"),
            ("POS", "CIRCLE %.8f %.8f %.5f" % (ra, dec, radius)),
            ("MAXREC", "8"),
            ("RESPONSEFORMAT", "JSON"),
        ]
        url = "https://%s/SIA?%s" % (IRSA_HOST, urlencode(params))
        try:
            request = Request(url, headers={"User-Agent": "CieloClaro/1.0 (local educational viewer)", "Accept": "application/json"})
            with urlopen(request, timeout=90) as response:
                body = response.read(24 * 1024 * 1024 + 1)
                if len(body) > 24 * 1024 * 1024:
                    self.send_json(502, {"error": "La respuesta de búsqueda de IRSA es demasiado grande."})
                    return
                # Keep the official JSON response unchanged for the browser to interpret.
                try:
                    json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json(502, {"error": "IRSA no devolvió JSON válido. Prueba desde el explorador oficial."})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as exc:
            self.send_json(502, {"error": "IRSA respondió HTTP %s." % exc.code})
        except (URLError, TimeoutError, OSError) as exc:
            self.send_json(502, {"error": "No se pudo conectar con IRSA: %s" % str(exc)})

    def handle_fits(self, query):
        target = query.get("url", [""])[0]
        if not is_official_fits_url(target):
            self.send_json(400, {"error": "El enlace debe ser un FITS oficial de IRSA/SPHEREx."})
            return
        too_large = {"error": "El FITS supera el límite local de 512 MB. Descárgalo desde IRSA y carga el archivo manualmente."}
        # Download fully (spilling to disk past 32 MB) before replying, so the browser always gets
        # either a complete file with an exact Content-Length or a JSON error, never a truncated FITS.
        try:
            with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as spool:
                request = Request(target, headers={"User-Agent": "CieloClaro/1.0 (local educational viewer)"})
                with FITS_OPENER.open(request, timeout=120) as response:
                    length = response.headers.get("Content-Length")
                    if length and length.isdigit() and int(length) > MAX_FITS_BYTES:
                        raise FitsTooLarge()
                    total = 0
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_FITS_BYTES:
                            raise FitsTooLarge()
                        spool.write(chunk)
                spool.seek(0)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/fits")
                    self.send_header("Content-Length", str(total))
                    self.send_header("Content-Disposition", "inline; filename=SPHEREx.fits")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    shutil.copyfileobj(spool, self.wfile, 1024 * 1024)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # The browser closed the connection (e.g. a newer search); nothing left to send.
                    self.close_connection = True
        except FitsTooLarge:
            self.send_json(413, too_large)
        except HTTPError as exc:
            self.send_json(502, {"error": "IRSA respondió HTTP %s al descargar el FITS." % exc.code})
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            self.send_json(502, {"error": "No se pudo descargar el FITS: %s" % str(exc)})

    def handle_object(self, query):
        try:
            ra = float(query.get("ra", [""])[0])
            dec = float(query.get("dec", [""])[0])
            if not (0 <= ra <= 360 and -90 <= dec <= 90):
                raise ValueError("Coordenadas fuera de rango.")
        except (TypeError, ValueError) as exc:
            self.send_json(400, {"error": str(exc) or "Coordenadas no válidas."})
            return

        # Identify only catalog entries very close to the requested coordinate.
        # A match is a nearby cataloged source, not an image classifier.
        point = "POINT('ICRS', ra, dec)"
        circle = "CIRCLE('ICRS', %.8f, %.8f, 0.003)" % (ra, dec)
        adql = (
            "SELECT TOP 3 main_id, otype, ra, dec, "
            "DISTANCE(POINT('ICRS', ra, dec), POINT('ICRS', %.8f, %.8f)) AS ang_sep "
            "FROM basic WHERE CONTAINS(%s, %s) = 1 ORDER BY ang_sep"
        ) % (ra, dec, point, circle)
        params = {"request": "doQuery", "lang": "adql", "format": "json", "query": adql}
        url = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync?%s" % urlencode(params)
        try:
            request = Request(url, headers={"User-Agent": "CieloClaro/1.0 (educational sky-coordinate lookup)", "Accept": "application/json"})
            with urlopen(request, timeout=45) as response:
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    self.send_json(502, {"error": "La respuesta del catálogo SIMBAD es demasiado grande."})
                    return
                try:
                    json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json(502, {"error": "SIMBAD no devolvió JSON válido."})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as exc:
            self.send_json(502, {"error": "SIMBAD respondió HTTP %s." % exc.code})
        except (URLError, TimeoutError, OSError) as exc:
            self.send_json(502, {"error": "No se pudo consultar SIMBAD: %s" % str(exc)})

    def handle_name(self, query):
        name = query.get("name", [""])[0].strip()
        if not name or len(name) > 100:
            self.send_json(400, {"error": "Escribe un nombre de hasta 100 caracteres."})
            return
        # Escape SQL string delimiters before building the SIMBAD identifier query.
        safe_name = name.replace("'", "''")
        adql = (
            "SELECT TOP 1 main_id, otype, ra, dec "
            "FROM basic JOIN ident ON oidref = oid WHERE id = '%s'"
        ) % safe_name
        params = {"request": "doQuery", "lang": "adql", "format": "json", "query": adql}
        url = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync?%s" % urlencode(params)
        try:
            request = Request(url, headers={"User-Agent": "CieloClaro/1.0 (educational object-name lookup)", "Accept": "application/json"})
            with urlopen(request, timeout=45) as response:
                body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    self.send_json(502, {"error": "La respuesta del catálogo SIMBAD es demasiado grande."})
                    return
                try:
                    json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_json(502, {"error": "SIMBAD no devolvió JSON válido."})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as exc:
            self.send_json(502, {"error": "SIMBAD respondió HTTP %s." % exc.code})
        except (URLError, TimeoutError, OSError) as exc:
            self.send_json(502, {"error": "No se pudo consultar SIMBAD: %s" % str(exc)})

    def serve_page(self, request_path):
        if request_path in ("/", "/index.html"):
            path = ROOT / "index.html"
        else:
            self.send_error(404, "No encontrado")
            return
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, "No se encontró index.html")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    print("Cielo Claro está listo en http://%s:%d" % (HOST, PORT))
    print("Este servidor escucha solo en esta computadora. Presiona Ctrl+C para cerrar.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        webbrowser.open("http://%s:%d" % (HOST, PORT))
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor cerrado.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
