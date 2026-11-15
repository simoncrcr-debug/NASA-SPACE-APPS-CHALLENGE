#!/usr/bin/env python3
"""Servidor local para AerosphereX: sirve la página y conecta con IRSA (SIA2, MOST), SIMBAD y JPL Horizons."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from pathlib import Path
import json
import posixpath
import re
import shutil
import tempfile
import time
import unicodedata
import webbrowser


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
MAX_FITS_BYTES = 512 * 1024 * 1024
IRSA_HOST = "irsa.ipac.caltech.edu"
# Image archives the local connector may download from: SPHEREx and NEOWISE single-exposure frames.
FITS_PATH_PREFIXES = ("/ibe/data/spherex/", "/ibe/data/wise/neowiser/p1bm_frm/")


def is_official_fits_url(url):
    """True only for https URLs on IRSA under the SPHEREx/NEOWISE data paths (no dot-segment escapes)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname != IRSA_HOST or parsed.port not in (None, 443):
        return False
    normalized = posixpath.normpath(parsed.path)
    return normalized.startswith(FITS_PATH_PREFIXES) and normalized == parsed.path.rstrip("/")


class OfficialFitsRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only while they stay on the official IRSA image data paths."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_official_fits_url(newurl):
            raise HTTPError(newurl, code, "Redirección fuera de los archivos de IRSA bloqueada.", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


FITS_OPENER = build_opener(OfficialFitsRedirectHandler)


def open_with_retry(request, timeout, attempts=3):
    """IRSA's cutout service answers 503 "Server busy, retry later" under load; wait briefly and retry."""
    for attempt in range(attempts):
        try:
            return FITS_OPENER.open(request, timeout=timeout)
        except HTTPError as exc:
            if exc.code not in (502, 503, 504) or attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))


class FitsTooLarge(Exception):
    pass


# ---- JPL Horizons: where was a Solar System body, seen from the telescope, at the frame's time? ----
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
# Horizons observer centers: spacecraft NAIF IDs (parallax matters for nearby bodies), geocenter otherwise.
OBSERVERS = {
    "wise": ("500@-163", "WISE/NEOWISE"),
    "spherex": ("500@-163182", "SPHEREx"),
    "earth": ("500@399", "centro de la Tierra"),
}
MAJOR_BODIES = {
    "sol": "10", "sun": "10", "luna": "301", "moon": "301",
    "mercurio": "199", "mercury": "199", "venus": "299", "marte": "499", "mars": "499",
    "jupiter": "599", "saturno": "699", "saturn": "699", "urano": "799", "uranus": "799",
    "neptuno": "899", "neptune": "899", "pluton": "999", "pluto": "999",
}
BODY_NAME_RE = re.compile(r"^[0-9A-Za-zÀ-ÿ(][0-9A-Za-zÀ-ÿ ./()\-_]{0,59}$")  # "(" for "(3122) Florence"
# Numbered periodic/interstellar comets ("3I", "3I Atlas", "1P/Halley") and provisional comet designations ("C/2025 N1").
NUMBERED_COMET_RE = re.compile(r"^(\d{1,4}[PI])(?:[\s/-].*)?$", re.IGNORECASE)
PROVISIONAL_COMET_RE = re.compile(r"^([PCDXI]/\d{4}\s+[A-Z]{1,2}\d{0,3})(?:\s*\(.*\))?$", re.IGNORECASE)
# Numbered asteroids as people and IRSA write them: "3122", "3122 Florence", "(3122) Florence".
PROVISIONAL_ASTEROID_RE = re.compile(r"^(\d{4})\s+([A-Za-z]{2}\d{0,4})$")
NUMBERED_ASTEROID_RE = re.compile(r"^\(?(\d{1,7})\)?(?:\s+[A-Za-zÀ-ÿ].*)?$")
# NAIF IDs as used in IRSA links (obj_naifid=20003122): 2000000+n / 20000000+n asteroids, 1000000+ comets.
NAIF_SMALL_BODY_RE = re.compile(r"^(?:naif\s*:?\s*)?([12]\d{6,7})$", re.IGNORECASE)


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def classify_body(name):
    """Normalize a free-text body name into one of: major, comet, asteroid_number, naif, text."""
    name = re.sub(r"\s+", " ", name.strip())
    key = strip_accents(name).lower()
    if key in MAJOR_BODIES:
        return {"kind": "major", "id": MAJOR_BODIES[key]}
    m = NAIF_SMALL_BODY_RE.match(name)
    if m:
        naif = m.group(1)
        if naif.startswith("2"):  # 20000000+n (current) or 2000000+n (legacy) numbered asteroid
            n = int(naif) - (20000000 if len(naif) == 8 else 2000000)
            if n > 0:
                return {"kind": "asteroid_number", "number": n, "naif": str(20000000 + n)}
        elif len(naif) == 7:  # 1000000+ comet record
            return {"kind": "naif", "naif": naif}
    m = NUMBERED_COMET_RE.match(name)
    if m:  # bare "1P" would match unrelated bodies, so always search it as a designation
        return {"kind": "comet", "des": m.group(1).upper()}
    m = PROVISIONAL_COMET_RE.match(name)
    if m:
        return {"kind": "comet", "des": re.sub(r"\s+", " ", m.group(1).upper())}
    m = PROVISIONAL_ASTEROID_RE.match(name)
    if m:  # "2024 YR4" is a designation, not asteroid number 2024
        return {"kind": "text", "name": "%s %s" % (m.group(1), m.group(2).upper())}
    m = NUMBERED_ASTEROID_RE.match(name)
    if m:
        n = int(m.group(1))
        return {"kind": "asteroid_number", "number": n, "naif": str(20000000 + n)}
    return {"kind": "text", "name": name}


def horizons_commands(name):
    """Ordered Horizons COMMAND candidates; the first one that yields an ephemeris wins."""
    body = classify_body(name)
    if body["kind"] == "major":
        return [body["id"]]
    if body["kind"] == "comet":
        return ["DES=%s;CAP;NOFRAG" % body["des"]]
    if body["kind"] == "asteroid_number":
        return ["%d;" % body["number"]]
    if body["kind"] == "naif":
        return [body["naif"]]
    # Trailing ';' restricts the lookup to small bodies (asteroids/comets), then fall back to major bodies.
    return [body["name"] + ";", body["name"]]


def query_horizons(command, center, jd):
    params = {
        "format": "json", "COMMAND": "'%s'" % command, "EPHEM_TYPE": "OBSERVER", "CENTER": "'%s'" % center,
        "TLIST": "'%.8f'" % jd, "QUANTITIES": "'1,9,19,20'", "OBJ_DATA": "NO", "MAKE_EPHEM": "YES",
        "ANG_FORMAT": "DEG", "CSV_FORMAT": "YES", "EXTRA_PREC": "YES",
    }
    request = Request("%s?%s" % (HORIZONS_URL, urlencode(params)), headers={"User-Agent": "AerosphereX/1.0 (educational ephemeris lookup)"})
    with urlopen(request, timeout=60) as response:
        body = response.read(1024 * 1024)
    return json.loads(body.decode("utf-8")).get("result", "")


def parse_horizons(result):
    """Return the single ephemeris row as a dict, or None when Horizons did not produce one."""
    start, end = result.find("$$SOE"), result.find("$$EOE")
    if start < 0 or end < 0:
        return None
    header = next((l for l in result[:start].splitlines() if "Date__(UT)" in l), "")
    row = result[start + 5:end].strip().splitlines()
    if not header or not row:
        return None
    cols = [c.strip() for c in header.split(",")]
    vals = [v.strip() for v in row[0].split(",")]
    data = dict(zip(cols, vals))

    def num(*keys):
        for k in keys:
            try:
                return float(data[k])
            except (KeyError, ValueError):
                continue
        return None

    target = ""
    for line in result.splitlines():
        if line.startswith("Target body name:"):
            target = re.sub(r"\s*\{source:.*$", "", line.split(":", 1)[1]).strip()
            break
    mag_type = "T-mag" if "T-mag" in data else ("APmag" if "APmag" in data else "")
    return {
        "target": target,
        "time_utc": data.get("Date__(UT)__HR:MN:SC.fff", ""),
        "ra": num(*[k for k in cols if k.startswith("R.A.") and "ICRF" in k]),
        "dec": num(*[k for k in cols if k.startswith("DEC") and "ICRF" in k]),
        "mag": num("T-mag", "APmag"),
        "mag_type": mag_type,
        "r_au": num("r"),
        "delta_au": num("delta"),
    }


# ---- IRSA MOST (Moving Object Search Tool): which archive frames contain a moving body? ----
MOST_URL = "https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most"
MOST_CATALOGS = {"spherex": "spherex", "neowise": "wise_neowiser"}
NEOWISE_FRAME_URL = "https://%s/ibe/data/wise/neowiser/p1bm_frm/%s/%s/%03d/%s%03d-w%s-int-1b.fits"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_MOST_FRAMES = 800


def most_params(name, catalog):
    body = classify_body(name)
    params = {"catalog": catalog, "output_mode": "Brief"}
    if body["kind"] == "major":
        params.update(input_type="naifid_input", obj_naifid=body["id"])
    elif body["kind"] in ("asteroid_number", "naif"):
        params.update(input_type="naifid_input", obj_naifid=body["naif"])
    elif body["kind"] == "comet":
        params.update(input_type="name_input", obj_name=body["des"])
    else:
        params.update(input_type="name_input", obj_name=body["name"])
    return params


def parse_ipac_table(text):
    """Parse an IPAC fixed-width table: '\\key = "value"' metadata plus rows sliced at the '|' header columns."""
    meta, rows, bars, names = {}, [], None, []
    for line in text.splitlines():
        if line.startswith("\\"):
            key, _, value = line[1:].partition("=")
            meta[key.strip()] = value.strip().strip('"').strip()
        elif line.startswith("|"):
            if bars is None:
                bars = [i for i, ch in enumerate(line) if ch == "|"]
                names = [line[bars[i] + 1:bars[i + 1]].strip() for i in range(len(bars) - 1)]
        elif line.strip() and bars:
            rows.append({names[i]: line[bars[i] + 1:bars[i + 1] + 1].strip() for i in range(len(names))})
    return meta, rows, bars is not None


def most_message(text):
    """Readable text from a MOST reply that is not a table (unknown or ambiguous names)."""
    plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:400]


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def most_frames(rows, mission):
    frames = []
    for r in rows:
        frame = {
            "ra": to_float(r.get("ra_obj")), "dec": to_float(r.get("dec_obj")),
            "vmag": to_float(r.get("vmag")), "sun_dist": to_float(r.get("sun_dist")),
            "geo_dist": to_float(r.get("geo_dist")), "phase": to_float(r.get("phase")),
        }
        if frame["ra"] is None or frame["dec"] is None:
            continue
        if mission == "neowise":
            scan, frame_num, band = r.get("scan_id", ""), r.get("frame_num", ""), r.get("band", "")
            if not (re.match(r"^\d{5}[a-z]$", scan) and frame_num.isdigit() and band in ("1", "2")):
                continue
            if r.get("qual_frame", "") == "0":  # frames flagged as unusable
                continue
            frame.update(date=r.get("date_obs", ""), mjd=to_float(r.get("mjd_obs")), band="W" + band,
                         id="%s%03d" % (scan, int(frame_num)),
                         url=NEOWISE_FRAME_URL % (IRSA_HOST, scan[-2:], scan, int(frame_num), scan, int(frame_num), band))
        else:
            uri = r.get("uri", "").lstrip("/")
            if not uri.startswith("ibe/data/spherex/"):
                continue
            frame.update(date=r.get("start_date", ""), mjd=to_float(r.get("mjd_start")), band=r.get("productid", ""),
                         id=r.get("observationid", ""), url="https://%s/%s" % (IRSA_HOST, uri),
                         exposure=to_float(r.get("exposure_time")))
        frames.append(frame)
    frames.sort(key=lambda f: f.get("mjd") or 0)
    return frames


def horizons_candidates(result):
    """Lines listing alternative matches when Horizons could not pick one body."""
    lines = [l.strip() for l in result.splitlines()]
    return [l for l in lines if re.match(r"^-?\d+\s+\S", l)][:6]


class Handler(BaseHTTPRequestHandler):
    server_version = "AerosphereX/1.0"

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
        elif parsed.path == "/api/ephem":
            self.handle_ephem(parse_qs(parsed.query))
        elif parsed.path == "/api/mos":
            self.handle_mos(parse_qs(parsed.query))
        elif parsed.path == "/api/health":
            self.send_json(200, {"ok": True, "service": "AerosphereX", "irsa": IRSA_HOST})
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
            request = Request(url, headers={"User-Agent": "AerosphereX/1.0 (local educational viewer)", "Accept": "application/json"})
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
            self.send_json(400, {"error": "El enlace debe ser un FITS oficial de IRSA (SPHEREx o NEOWISE)."})
            return
        too_large = {"error": "El FITS supera el límite local de 512 MB. Descárgalo desde IRSA y carga el archivo manualmente."}
        # Download fully (spilling to disk past 32 MB) before replying, so the browser always gets
        # either a complete file with an exact Content-Length or a JSON error, never a truncated FITS.
        try:
            with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as spool:
                request = Request(target, headers={"User-Agent": "AerosphereX/1.0 (local educational viewer)"})
                with open_with_retry(request, timeout=120) as response:
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
            request = Request(url, headers={"User-Agent": "AerosphereX/1.0 (educational sky-coordinate lookup)", "Accept": "application/json"})
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
            request = Request(url, headers={"User-Agent": "AerosphereX/1.0 (educational object-name lookup)", "Accept": "application/json"})
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

    def handle_mos(self, query):
        name = query.get("name", [""])[0].strip()
        mission = query.get("mission", ["spherex"])[0].strip().lower()
        begin = query.get("begin", [""])[0].strip()
        end = query.get("end", [""])[0].strip()
        if not BODY_NAME_RE.match(name):
            self.send_json(400, {"error": "Escribe un nombre o designación válido (p. ej. 3I Atlas, Florence, Apophis)."})
            return
        if mission not in MOST_CATALOGS or (begin and not DATE_RE.match(begin)) or (end and not DATE_RE.match(end)):
            self.send_json(400, {"error": "Misión o fechas no válidas."})
            return
        params = most_params(name, MOST_CATALOGS[mission])
        if begin:
            params["obs_begin"] = begin
        if end:
            params["obs_end"] = end
        try:
            request = Request("%s?%s" % (MOST_URL, urlencode(params)), headers={"User-Agent": "AerosphereX/1.0 (educational moving-object search)"})
            with urlopen(request, timeout=150) as response:
                text = response.read(32 * 1024 * 1024).decode("utf-8", "replace")
        except HTTPError as exc:
            if exc.code == 400:  # MOST answers 400 for names it cannot resolve to a Solar System body
                detail = most_message(exc.read(64 * 1024).decode("utf-8", "replace"))
                self.send_json(404, {"error": "IRSA no reconoció «%s» como asteroide o cometa%s" % (name, (": " + detail) if detail else ".")})
                return
            self.send_json(502, {"error": "IRSA MOST respondió HTTP %s." % exc.code})
            return
        except (URLError, TimeoutError, OSError) as exc:
            self.send_json(502, {"error": "No se pudo consultar IRSA MOST: %s" % str(exc)})
            return
        meta, rows, is_table = parse_ipac_table(text)
        if not is_table:
            self.send_json(404, {"error": "IRSA no reconoció «%s»: %s" % (name, most_message(text) or "sin detalles")})
            return
        frames = most_frames(rows, mission)
        total = len(frames)
        if total > MAX_MOST_FRAMES:  # keep the whole time span: sample evenly instead of cutting the end
            step = total / MAX_MOST_FRAMES
            frames = [frames[int(i * step)] for i in range(MAX_MOST_FRAMES)]
        self.send_json(200, {
            "query": name, "mission": mission, "object": meta.get("object_name", name),
            "total": total, "sampled": total > MAX_MOST_FRAMES, "frames": frames,
        })

    def handle_ephem(self, query):
        name = query.get("name", [""])[0].strip()
        observer = query.get("observer", ["earth"])[0].strip().lower()
        try:
            jd = float(query.get("jd", [""])[0])
        except ValueError:
            jd = float("nan")
        if not BODY_NAME_RE.match(name):
            self.send_json(400, {"error": "Escribe un nombre o designación válido (p. ej. 3I, Ceres, 1P/Halley)."})
            return
        if not (2378496.5 <= jd <= 2524593.5):  # Horizons small-body range used here: years 1800-2200
            self.send_json(400, {"error": "Fecha de observación no válida."})
            return
        center, observer_label = OBSERVERS.get(observer, OBSERVERS["earth"])
        last_result = ""
        try:
            for command in horizons_commands(name):
                last_result = query_horizons(command, center, jd)
                ephem = parse_horizons(last_result)
                if ephem and ephem["ra"] is not None and ephem["dec"] is not None:
                    ephem.update({"query": name, "observer": observer_label, "center": center, "jd": jd, "source": "JPL Horizons"})
                    self.send_json(200, ephem)
                    return
        except HTTPError as exc:
            self.send_json(502, {"error": "JPL Horizons respondió HTTP %s." % exc.code})
            return
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            self.send_json(502, {"error": "No se pudo consultar JPL Horizons: %s" % str(exc)})
            return
        candidates = horizons_candidates(last_result)
        message = "JPL Horizons no encontró un único cuerpo llamado «%s»." % name
        if candidates:
            message += " Coincidencias: " + "; ".join(candidates)
        self.send_json(404, {"error": message, "candidates": candidates})

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
    print("AerosphereX está listo en http://%s:%d" % (HOST, PORT))
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
