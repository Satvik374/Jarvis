"""OCR text detection, with a backend chain that needs no extra install.

Strategies for reading the screen
---------------------------------
The primary reader is UI Automation, which names real controls and gives exact
bounding boxes. But a control with **no accessible name** - an icon button, a
canvas, a game surface, an image - is invisible to it, so no wording can ever
resolve. OCR reads the pixels instead, which is what makes a *spoken* name able
to reach those controls.

Backends, tried in order, each optional and imported lazily:

* **Windows OCR** (``winrt-Windows.Media.Ocr``): the engine built into Windows
  itself. No model files to ship or download, ~0.7s, per-word bounding boxes.
* **rapidocr_onnxruntime**: bundled ONNX models, no torch, ~1.1s.
* **easyocr**: the heaviest (pulls torch), kept only as a last resort.
* **pytesseract**: only when the ``tesseract`` binary is on PATH.

The first backend that runs is remembered, so the ~0.7s engine probe happens
once rather than on every read. If the remembered backend later fails it is
forgotten and the chain is re-probed, so a transient engine fault degrades to
the next backend instead of a silent dead end.

Nothing here raises into a voice session: an unavailable or failing backend is
skipped, and an empty list means "no text found", never an exception the caller
has to swallow.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

#: (left, top, right, bottom), text, confidence 0..1
Box = tuple[tuple[int, int, int, int], str, float]

_LOCK = threading.Lock()
_PREFERRED: dict[str, Any] = {"name": None, "fn": None}


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

def _windows_ocr(image: Any) -> list[Box]:
    """Windows' built-in OCR. Raises when no OCR language pack is installed."""
    import asyncio
    import io

    import winrt.windows.graphics.imaging as imaging  # type: ignore
    import winrt.windows.media.ocr as ocr_mod  # type: ignore
    import winrt.windows.storage.streams as streams  # type: ignore

    async def run() -> list[Box]:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        stream = streams.InMemoryRandomAccessStream()
        writer = streams.DataWriter(stream)
        writer.write_bytes(buffer.getvalue())
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)

        decoder = await imaging.BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        engine = ocr_mod.OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            # No language pack - let the chain try a real OCR backend instead of
            # caching an engine that can only ever return nothing.
            raise RuntimeError("Windows OCR has no available language pack")

        result = await engine.recognize_async(bitmap)
        boxes: list[Box] = []
        for line in result.lines:
            for word in line.words:
                rect = word.bounding_rect
                boxes.append((
                    (int(rect.x), int(rect.y),
                     int(rect.x + rect.width), int(rect.y + rect.height)),
                    word.text,
                    1.0,      # the engine reports no per-word confidence
                ))
        return boxes

    return _run_coroutine(run())


def _run_coroutine(coroutine: Any) -> Any:
    """Await ``coroutine`` whether or not an event loop is already running.

    The voice session runs inside an event loop, and ``asyncio.run`` raises
    there. Running the coroutine on a worker thread with its own loop keeps the
    read working from both the synchronous tool path and the live session.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["value"] = asyncio.run(coroutine)
        except BaseException as exc:  # re-raised on the calling thread
            outcome["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _rapidocr(image: Any) -> list[Box]:
    import numpy as np  # type: ignore

    engine = _rapidocr_engine()
    result, _elapsed = engine(np.array(image.convert("RGB")))
    boxes: list[Box] = []
    for item in (result or []):
        quad, text, confidence = item[0], item[1], item[2]
        xs = [int(point[0]) for point in quad]
        ys = [int(point[1]) for point in quad]
        boxes.append((
            (min(xs), min(ys), max(xs), max(ys)),
            str(text),
            float(confidence),
        ))
    return boxes


_RAPIDOCR_SINGLETON: Any = None


def _rapidocr_engine() -> Any:
    global _RAPIDOCR_SINGLETON
    if _RAPIDOCR_SINGLETON is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        _RAPIDOCR_SINGLETON = RapidOCR()
    return _RAPIDOCR_SINGLETON


def _easyocr(image: Any) -> list[Box]:
    import numpy as np  # type: ignore

    engine = _easyocr_reader()
    result = engine.readtext(np.array(image.convert("RGB")))
    boxes: list[Box] = []
    for quad, text, confidence in result:
        xs = [int(point[0]) for point in quad]
        ys = [int(point[1]) for point in quad]
        boxes.append((
            (min(xs), min(ys), max(xs), max(ys)),
            str(text),
            float(confidence),
        ))
    return boxes


_EASYOCR_SINGLETON: Any = None


def _easyocr_reader() -> Any:
    global _EASYOCR_SINGLETON
    if _EASYOCR_SINGLETON is None:
        import easyocr  # type: ignore

        _EASYOCR_SINGLETON = easyocr.Reader(["en"], gpu=False)
    return _EASYOCR_SINGLETON


def _pytesseract(image: Any) -> list[Box]:
    """Only usable when the ``tesseract`` binary is on PATH."""
    import pytesseract  # type: ignore
    from pytesseract import Output  # type: ignore

    data = pytesseract.image_to_data(image.convert("RGB"), output_type=Output.DICT)
    boxes: list[Box] = []
    for i, text in enumerate(data.get("text", [])):
        text = str(text).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][i]) / 100.0
        except (KeyError, IndexError, TypeError, ValueError):
            confidence = 0.0
        left, top = int(data["left"][i]), int(data["top"][i])
        width, height = int(data["width"][i]), int(data["height"][i])
        boxes.append((
            (left, top, left + width, top + height), text, max(0.0, confidence)
        ))
    return boxes


#: Order matters: cheapest and most accurate first.
_BACKENDS: tuple[tuple[str, Callable[[Any], list[Box]]], ...] = (
    ("windows", _windows_ocr),
    ("rapidocr", _rapidocr),
    ("easyocr", _easyocr),
    ("pytesseract", _pytesseract),
)


# --------------------------------------------------------------------------
# Public surface
# --------------------------------------------------------------------------

def read_boxes(image: Any) -> list[Box]:
    """Every text box OCR can read in ``image``, as ``(bbox, text, confidence)``.

    Returns ``[]`` when no backend is usable or no text is found; never raises,
    because the caller is a voice session that must keep talking.
    """
    with _LOCK:
        preferred_name = _PREFERRED["name"]
        preferred_fn = _PREFERRED["fn"]

    order = _BACKENDS
    if preferred_fn is not None:
        order = ((preferred_name, preferred_fn),) + tuple(
            backend for backend in _BACKENDS if backend[0] != preferred_name
        )

    for name, backend in order:
        try:
            boxes = backend(image)
        except Exception:
            if name == preferred_name:
                # The remembered backend broke; re-probe the chain next time.
                with _LOCK:
                    _PREFERRED["name"] = None
                    _PREFERRED["fn"] = None
            continue
        with _LOCK:
            _PREFERRED["name"] = name
            _PREFERRED["fn"] = backend
        return boxes
    return []


def _probe_image() -> Any:
    from PIL import Image, ImageDraw  # type: ignore

    image = Image.new("RGB", (160, 60), (255, 255, 255))
    ImageDraw.Draw(image).text((10, 20), "ok", fill=(0, 0, 0))
    return image


def available() -> bool:
    """True when some OCR backend can read text, so callers can explain absence."""
    return bool(read_boxes(_probe_image()))


def engine_name() -> str:
    """The backend currently in use, or ``"none"``. For diagnostics."""
    with _LOCK:
        return _PREFERRED["name"] or "none"


def reset() -> None:
    """Forget the remembered backend. For tests."""
    with _LOCK:
        _PREFERRED["name"] = None
        _PREFERRED["fn"] = None
