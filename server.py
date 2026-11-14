"""Servidor local de Cosmic Hunter. Ejecutar con: python server.py"""
from __future__ import annotations

import io
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, unquote, urlparse
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from spectrum_engine import IMAGE_SUFFIXES, list_images, read_image, spectrum, write_csv

ROOT = Path(__file__).resolve().parent
PORT = 8765
STATIC = {"index.html", "style.css", "advanced.css", "app.js"}


def find_image(name: str) -> Path | None:
    if Path(name).name != name:
        return None
    return next((p for p in list_images(ROOT) if p.name == name), None)


class Handler(BaseHTTPRequestHandler):
    server_version = "CosmicHunterLocal/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print("[Cosmic Hunter] " + fmt % args)

    def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data: object, status: int = 200) -> None:
        self.send_bytes(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                        "application/json; charset=utf-8", status)

    def jpl_json(self, endpoint: str, params: dict[str, str]) -> dict:
        # The official SSD API documentation advises against browser-side embedding;
        # make the request from the local Python service and return the JSON response.
        url = "https://ssd-api.jpl.nasa.gov/" + endpoint + "?" + urlencode(params)
        request = Request(url, headers={"User-Agent": "CosmicHunterLocal/1.0 (educational prototype)"})
        with urlopen(request, timeout=18) as response:
            return json.loads(response.read().decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/cad":
            try:
                days = int(parse_qs(parsed.query).get("days", ["30"])[0])
                if days not in {30, 90, 365}:
                    raise ValueError("El plazo debe ser 30, 90 o 365 días.")
                data = self.jpl_json("cad.api", {"date-min": "now", "date-max": f"+{days}",
                    "dist-max": "0.05", "sort": "date", "limit": "20",
                    "diameter": "true", "fullname": "true"})
                return self.send_json(data)
            except (ValueError, HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                return self.send_json({"error": f"NASA/JPL no está disponible: {exc}"}, 502)
        if path == "/api/sbdb":
            try:
                query = parse_qs(parsed.query).get("name", [""])[0].strip()
                if not query or len(query) > 80:
                    raise ValueError("Escribe un nombre o designación de hasta 80 caracteres.")
                data = self.jpl_json("sbdb.api", {"sstr": query, "phys-par": "true",
                    "ca-data": "true", "ca-body": "Earth", "ca-time": "cd",
                    "vi-data": "true", "discovery": "true"})
                return self.send_json(data)
            except (ValueError, HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                return self.send_json({"error": f"NASA/JPL no está disponible: {exc}"}, 502)
        if path == "/api/images":
            images = []
            for p in list_images(ROOT):
                try:
                    if p.suffix.lower() in {".fits", ".fit", ".fts"}:
                        array, fmt = read_image(p)
                        height, width = array.shape
                        mode = fmt
                    else:
                        with Image.open(p) as im:
                            width, height = im.size
                            mode = im.mode
                    images.append({"name": p.name, "width": width, "height": height,
                                   "mode": mode, "bytes": p.stat().st_size})
                except Exception as exc:
                    images.append({"name": p.name, "error": str(exc), "bytes": p.stat().st_size})
            return self.send_json({"images": images, "count": len(images)})
        if path.startswith("/api/images/"):
            name = path.removeprefix("/api/images/")
            image_path = find_image(name)
            if image_path is None:
                return self.send_json({"error": "Imagen no disponible."}, 404)
            try:
                if image_path.suffix.lower() in {".fits", ".fit", ".fts"}:
                    array, _ = read_image(image_path)
                    finite = array[np.isfinite(array)]
                    if finite.size == 0:
                        raise ValueError("FITS sin píxeles finitos para previsualizar.")
                    lo, hi = np.percentile(finite, [1, 99.5])
                    preview = np.clip((array - lo) / max(float(hi - lo), 1e-12), 0, 1)
                    preview = np.arcsinh(10 * preview) / np.arcsinh(10)
                    buf = io.BytesIO()
                    Image.fromarray(np.uint8(preview * 255), mode="L").save(buf, format="PNG")
                    return self.send_bytes(buf.getvalue(), "image/png")
                mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
                return self.send_bytes(image_path.read_bytes(), mime)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 422)
        if path.startswith("/api/results/"):
            name = Path(path.removeprefix("/api/results/")).name
            if name != path.removeprefix("/api/results/") or not re.fullmatch(r"espectro_pixel_[\w.-]+\.csv", name):
                return self.send_json({"error": "Archivo de resultado no válido."}, 404)
            output = (ROOT / "resultados_espectros" / name).resolve()
            if output.parent != (ROOT / "resultados_espectros").resolve() or not output.is_file():
                return self.send_json({"error": "No se encontró el CSV."}, 404)
            return self.send_bytes(output.read_bytes(), "text/csv; charset=utf-8")
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        if name not in STATIC:
            return self.send_json({"error": "No encontrado."}, 404)
        file_path = ROOT / name
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        return self.send_bytes(file_path.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or "javascript" in mime else ""))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/spectrum":
            return self.send_json({"error": "Ruta no encontrada."}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 128_000:
                raise ValueError("Solicitud vacía o demasiado grande.")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            x, y = int(body["x"]), int(body["y"])
            radius = int(body.get("radius", 0))
            if radius < 0 or radius > 100:
                raise ValueError("El radio debe estar entre 0 y 100 píxeles.")
            result = spectrum(ROOT, body.get("files", []), x, y, radius,
                              body.get("reference_name"), body.get("axis_mode", "index"))
            write_csv(result, ROOT / "resultados_espectros")
            return self.send_json(result)
        except (ValueError, KeyError, json.JSONDecodeError, RuntimeError, OSError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            return self.send_json({"error": f"No se pudo calcular el espectro: {exc}"}, 500)


if __name__ == "__main__":
    print(f"Cosmic Hunter disponible en http://127.0.0.1:{PORT}")
    print(f"Imágenes detectadas: {len(list_images(ROOT))} (raíz o carpeta fotos/)")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
