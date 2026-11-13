"""Typed image-quality analyses built on CODE V's macro functions.

Each returns numbers, not listing text. CODE V signals a failed calculation
with a negative return value rather than an error, so every row is checked
and a failed row makes the whole envelope ``ok: false``.

Signatures follow the CODE V API Reference Guide (MTF_1FLD, RMS_1FLD) and the
Macro-PLUS Reference Manual (RMSSPOT).
"""

from __future__ import annotations

from typing import Any

from codev_mcp.engine import CodeVEngine

# CVMTFTypeEnum, CVMTFWaveEnum, CVRMSFocusModeEnum (values from the type library)
MTF_DIF, MTF_GEO = 0, 1
MTF_SIW, MTF_SQW = 0, 1
FOCUS_NOM, FOCUS_BES = 0, 1


def _fields(cv: Any, fields: list[int] | None) -> list[int]:
    n = int(cv.FieldCount)
    if not fields:
        return list(range(1, n + 1))
    bad = [f for f in fields if not 1 <= f <= n]
    if bad:
        raise ValueError(f"Field numbers {bad} are outside 1..{n}.")
    return list(fields)


def _check(cv: Any, zoom: int, wavelength: int | None = None) -> None:
    if not 1 <= zoom <= int(cv.ZoomCount):
        raise ValueError(f"Zoom {zoom} is outside 1..{int(cv.ZoomCount)}.")
    if wavelength is not None and not 0 <= wavelength <= int(cv.WavelengthCount):
        raise ValueError(f"Wavelength {wavelength} is outside 0..{int(cv.WavelengthCount)}.")


def _finish(rows: list[dict], **extra: Any) -> dict:
    result: dict[str, Any] = {**extra, "results": rows}
    failed = [r for r in rows if "error" in r]
    if failed:
        result["ok"] = False
        result["failed_rows"] = len(failed)
    return result


def rms_spot(engine: CodeVEngine, zoom: int, fields: list[int] | None) -> dict:
    def fn(cv: Any) -> dict:
        _check(cv, zoom)
        rows = []
        for f in _fields(cv, fields):
            value = float(cv.EvaluateExpression(f"RMSSPOT({zoom},{f},1,0.01)"))
            row: dict[str, Any] = {"field": f}
            if value < 0:
                row["error"] = (
                    "ray trace failure or no rays through the system"
                    if value > -1.5
                    else "ray grid not fully clipped: apertures and vignetting disagree"
                )
            else:
                row["rms_spot_diameter"] = value
            rows.append(row)
        return _finish(rows, zoom=zoom, units="lens units", note="Polychromatic, nominal focus.")

    return engine.with_cv(fn)


def wavefront_error(
    engine: CodeVEngine,
    zoom: int,
    wavelength: int,
    nrd: int,
    best_focus: bool,
    fields: list[int] | None,
) -> dict:
    def fn(cv: Any) -> dict:
        _check(cv, zoom, wavelength)
        mode = FOCUS_BES if best_focus else FOCUS_NOM
        rows = []
        for f in _fields(cv, fields):
            res = cv.RMS_1FLD(zoom, f, wavelength, nrd, mode)
            row: dict[str, Any] = {"field": f}
            if float(res("Result")) < 0:
                row["error"] = "calculation failed (input error or too few rays)"
            else:
                out = res("Output")
                row.update(
                    rms_waves=float(out[0]),
                    focus_shift=float(out[1]),
                    strehl=float(out[4]),
                    wavelength_nm=float(out[5]),
                )
            rows.append(row)
        return _finish(
            rows,
            zoom=zoom,
            wavelength="polychromatic" if wavelength == 0 else wavelength,
            focus="best per field" if best_focus else "nominal",
            note="Tilt removed. focus_shift is 0 at nominal focus.",
        )

    return engine.with_cv(fn)


def mtf(
    engine: CodeVEngine,
    frequencies: list[float],
    zoom: int,
    fields: list[int] | None,
    diffraction: bool,
    nrd: int,
) -> dict:
    def fn(cv: Any) -> dict:
        _check(cv, zoom)
        if not frequencies:
            raise ValueError("Give at least one spatial frequency.")
        kind = MTF_DIF if diffraction else MTF_GEO
        rows = []
        for f in _fields(cv, fields):
            for freq in frequencies:
                row: dict[str, Any] = {"field": f, "frequency": float(freq)}
                for azimuth in (0.0, 90.0):
                    res = cv.MTF_1FLD(zoom, f, float(freq), azimuth, nrd, kind, MTF_SIW, 0)
                    key = f"azimuth_{int(azimuth)}"
                    if float(res("Result")) < 0:
                        row["error"] = f"calculation failed at azimuth {int(azimuth)}"
                        break
                    out = res("Output")
                    row[key] = {"modulation": float(out[0]), "diffraction_limit": float(out[3])}
                rows.append(row)
        return _finish(
            rows,
            zoom=zoom,
            type="diffraction" if diffraction else "geometrical",
            frequency_units="cycles/mm at the image (cycles per angular unit if afocal)",
            note="Polychromatic, sine-wave object, nominal focus. Azimuth is the "
            "orientation of the spatial frequency at the image, in degrees.",
        )

    return engine.with_cv(fn)
