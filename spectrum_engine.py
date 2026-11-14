"""Medición reproducible de una posición/apertura en imágenes multibanda.

Las PNG/JPEG se consideran valores digitales de visualización, no flujo calibrado.
Para magnitudes científicas use FITS calibrados con sus productos de varianza y WCS.
"""
from __future__ import annotations

import csv
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".fits", ".fit", ".fts"}
NUMERIC_NAME = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p.lower() for p in NUMERIC_NAME.split(name))


def file_number(name: str) -> int | None:
    match = re.search(r"(\d+)(?=\.[^.]+$)", name)
    return int(match.group(1)) if match else None


def list_images(root: Path) -> list[Path]:
    """List supported images in the project folder and its fotos/ subfolder."""
    paths: list[Path] = []
    for folder in (root, root / "fotos"):
        if folder.exists():
            paths.extend(p for p in folder.iterdir()
                         if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
                         and not p.name.startswith("."))
    # Resolve and deduplicate so a path cannot escape the workspace.
    safe: dict[Path, Path] = {}
    for path in paths:
        resolved = path.resolve()
        if resolved.parent in {root.resolve(), (root / "fotos").resolve()}:
            safe[resolved] = resolved
    return sorted(safe.values(), key=lambda p: natural_key(p.name))


def read_image(path: Path) -> tuple[np.ndarray, str]:
    """Read a 2-D image; FITS support uses Astropy when installed."""
    if path.suffix.lower() in {".fits", ".fit", ".fts"}:
        try:
            from astropy.io import fits  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Para leer FITS instala Astropy: pip install astropy") from exc
        with fits.open(path, memmap=False) as hdul:
            data = next((hdu.data for hdu in hdul if getattr(hdu, "data", None) is not None
                         and getattr(hdu.data, "ndim", 0) >= 2), None)
            if data is None:
                raise ValueError("El FITS no contiene una imagen bidimensional.")
            array = np.asarray(data, dtype=np.float64).squeeze()
            if array.ndim != 2:
                raise ValueError("Solo se admite una imagen FITS 2D por archivo.")
            return array, "FITS"
    with Image.open(path) as im:
        if im.mode in ("1", "L", "I", "I;16", "F"):
            array = np.asarray(im, dtype=np.float64)
        else:
            # Luma conversion for viewing RGB inputs; this is not photometric calibration.
            rgb = np.asarray(im.convert("RGB"), dtype=np.float64)
            array = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
        if array.ndim != 2:
            raise ValueError("No se pudo convertir la imagen a una matriz 2D.")
        return array, f"{im.format or path.suffix[1:].upper()}"


def _coarse_plane(image: np.ndarray, side: int = 384) -> np.ndarray:
    """Downsample, log-compress and high-pass for translation registration."""
    h, w = image.shape
    scale = min(side / h, side / w, 1.0)
    nh, nw = max(16, int(h * scale)), max(16, int(w * scale))
    im = Image.fromarray(np.nan_to_num(image, nan=0.0).astype(np.float32))
    im.thumbnail((nw, nh), Image.Resampling.BILINEAR)
    small = np.asarray(im, dtype=np.float64)
    finite = small[np.isfinite(small)]
    if finite.size == 0:
        raise ValueError("La imagen no contiene píxeles finitos.")
    low, high = np.percentile(finite, [1, 99.8])
    small = np.clip((small - low) / max(high - low, 1e-12), 0, 1)
    small = np.log1p(12 * small)
    small -= np.median(small)
    return small


def estimate_translation(reference: np.ndarray, image: np.ndarray) -> tuple[float, float, float]:
    """Estimate an integer x/y translation by phase correlation.

    Returns shift_x, shift_y to apply to image, and a normalized alignment score.
    Rotation, scale, distortion, and WCS registration are not modeled.
    """
    if reference.shape != image.shape:
        raise ValueError("Las imágenes tienen tamaños distintos; no se pueden alinear automáticamente.")
    a, b = _coarse_plane(reference), _coarse_plane(image)
    if a.shape != b.shape:
        raise ValueError("No fue posible comparar los tamaños de las imágenes.")
    fa, fb = np.fft.fft2(a), np.fft.fft2(b)
    cross = fa * np.conj(fb)
    magnitude = np.abs(cross)
    corr = np.fft.ifft2(cross / np.maximum(magnitude, 1e-12)).real
    py, px = np.unravel_index(np.argmax(corr), corr.shape)
    dy = int(py if py <= corr.shape[0] // 2 else py - corr.shape[0])
    dx = int(px if px <= corr.shape[1] // 2 else px - corr.shape[1])
    # Phase correlation returns the shift that moves the moving image into the reference frame.
    scale_y = reference.shape[0] / corr.shape[0]
    scale_x = reference.shape[1] / corr.shape[1]
    shift_y, shift_x = dy * scale_y, dx * scale_x
    shifted = np.roll(image, (int(round(shift_y)), int(round(shift_x))), axis=(0, 1))
    # Correlation on robustly clipped low-resolution planes, useful as a warning only.
    aa, bb = _coarse_plane(reference).ravel(), _coarse_plane(shifted).ravel()
    score = float(np.corrcoef(aa, bb)[0, 1]) if aa.size > 1 else 0.0
    if not math.isfinite(score):
        score = 0.0
    return shift_x, shift_y, score


def measure_pixel(image: np.ndarray, x: int, y: int, radius: int = 0) -> dict:
    """Measure one pixel, or a circular aperture with a local annulus background."""
    h, w = image.shape
    if x < 0 or y < 0 or x >= w or y >= h:
        raise ValueError(f"El píxel ({x}, {y}) queda fuera de la imagen ({w}×{h}).")
    clean = np.asarray(image, dtype=np.float64)
    yy, xx = np.ogrid[:h, :w]
    r2 = (xx - x) ** 2 + (yy - y) ** 2
    if radius <= 0:
        value = float(clean[y, x])
        return {"signal": value, "pixel_value": value, "background": None,
                "pixels": 1, "method": "pixel"}
    aperture = r2 <= radius ** 2
    annulus = (r2 >= max(radius + 2, radius * 2) ** 2) & (r2 <= (radius * 3) ** 2)
    annulus_values = clean[annulus & np.isfinite(clean)]
    background = float(np.median(annulus_values)) if annulus_values.size else float(np.nanmedian(clean))
    values = clean[aperture & np.isfinite(clean)]
    signal = float(np.sum(values - background))
    return {"signal": signal, "pixel_value": float(clean[y, x]),
            "background": background, "pixels": int(values.size), "method": "apertura"}


def spectrum(root: Path, files: Iterable[dict], x: int, y: int, radius: int = 0,
             reference_name: str | None = None, axis_mode: str = "index") -> dict:
    """Measure selected bands at one position and write an honest sampled-spectrum CSV.

    Each file entry: {"name": str, "wavelength_nm": float}.
    No interpolation is performed; each plotted point is one supplied image.
    """
    entries = list(files)
    if len(entries) < 2:
        raise ValueError("Selecciona al menos dos imágenes.")
    if axis_mode not in {"index", "wavelength"}:
        raise ValueError("El eje debe ser índice de imagen o longitud de onda.")
    known = {p.name: p for p in list_images(root)}
    parsed: list[tuple[float | None, str, Path]] = []
    for item in entries:
        name = Path(str(item.get("name", ""))).name
        if name not in known:
            raise ValueError(f"No se encontró la imagen autorizada: {name}")
        wavelength = None
        if axis_mode == "wavelength":
            try:
                wavelength = float(item.get("wavelength_nm"))
            except (TypeError, ValueError):
                raise ValueError(f"Falta la longitud de onda de {name}.")
            if not math.isfinite(wavelength) or wavelength <= 0:
                raise ValueError(f"La longitud de onda de {name} debe ser positiva.")
        parsed.append((wavelength, name, known[name]))
    if axis_mode == "wavelength" and len({round(float(v[0]), 8) for v in parsed}) != len(parsed):
        raise ValueError("Cada imagen necesita una longitud de onda distinta.")
    parsed.sort(key=(lambda v: float(v[0])) if axis_mode == "wavelength" else lambda v: natural_key(v[1]))

    ref_path = next((p for _, name, p in parsed if name == reference_name), None)
    if ref_path is None:
        # A selected reference can be used even if it is not itself one of the plotted bands.
        ref_path = known.get(Path(reference_name).name) if reference_name else parsed[0][2]
    if ref_path is None:
        raise ValueError("No se encontró la imagen de referencia elegida.")
    reference, reference_format = read_image(ref_path)
    if x < 0 or y < 0 or y >= reference.shape[0] or x >= reference.shape[1]:
        raise ValueError(f"El píxel seleccionado debe estar dentro de {reference.shape[1]}×{reference.shape[0]}.")
    rows, warnings = [], []
    file_numbers = [file_number(name) for _, name, _ in parsed]
    use_file_numbers = all(v is not None for v in file_numbers) and len(set(file_numbers)) == len(file_numbers)
    for i, (wavelength, name, path) in enumerate(parsed):
        image, fmt = read_image(path)
        if image.shape != reference.shape:
            warnings.append(f"{name}: tamaño distinto; no se midió.")
            continue
        if path.resolve() == ref_path.resolve():
            dx, dy, score = 0.0, 0.0, 1.0
            aligned = image
        else:
            dx, dy, score = estimate_translation(reference, image)
            aligned = np.roll(image, (int(round(dy)), int(round(dx))), axis=(0, 1))
            if score < 0.15:
                warnings.append(f"{name}: alineación incierta (r={score:.2f}); verifica que sea el mismo campo.")
            if max(abs(dx), abs(dy)) > min(image.shape) * 0.2:
                warnings.append(f"{name}: desplazamiento grande ({dx:.0f}, {dy:.0f} px); verifica el registro.")
        m = measure_pixel(aligned, x, y, radius)
        image_index = file_numbers[i] if use_file_numbers else i + 1
        rows.append({"wavelength_nm": wavelength, "axis_value": wavelength if axis_mode == "wavelength" else image_index,
                     "band_index": image_index, "file": name, **m,
                     "format": fmt, "shift_x_px": dx, "shift_y_px": dy,
                     "alignment_r": score})
    if len(rows) < 2:
        raise ValueError("No hay dos imágenes compatibles para graficar.")
    positive = [r["signal"] for r in rows if r["signal"] > 0]
    peak = max(positive) if positive else None
    for row in rows:
        row["relative_to_peak"] = row["signal"] / peak if peak else None
    return {"rows": rows, "warnings": sorted(set(warnings)),
            "position": {"x": x, "y": y, "radius_px": radius},
            "reference_image": ref_path.name,
            "axis_mode": axis_mode,
            "measurement_note": ("Valores digitales de los archivos; no equivalen a flujo calibrado. Solo se grafican bandas medidas, sin interpolación." if axis_mode == "wavelength" else "Perfil de señal digital por orden de archivo; no es un eje de longitud de onda ni flujo calibrado. No se interpolaron bandas."),
            "output": None}


def write_csv(result: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"espectro_pixel_{result['position']['x']}_{result['position']['y']}_{stamp}.csv"
    fields = ["numero_en_nombre_archivo", "longitud_onda_nm", "archivo", "x_pixel", "y_pixel", "radio_apertura_px",
              "senal_digital_sin_calibrar", "pixel_central", "fondo_local",
              "pixeles_apertura", "relativa_al_maximo", "desplazamiento_x_px",
              "desplazamiento_y_px", "correlacion_alineacion"]
    p = result["position"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(fields)
        for row in result["rows"]:
            writer.writerow([row["band_index"], "" if row["wavelength_nm"] is None else row["wavelength_nm"],
                             row["file"], p["x"], p["y"], p["radius_px"],
                             f"{row['signal']:.8g}", f"{row['pixel_value']:.8g}",
                             "" if row["background"] is None else f"{row['background']:.8g}",
                             row["pixels"], "" if row["relative_to_peak"] is None else f"{row['relative_to_peak']:.8g}",
                             f"{row['shift_x_px']:.3f}", f"{row['shift_y_px']:.3f}",
                             f"{row['alignment_r']:.5f}"])
    result["output"] = path.name
    return path
