"""Archive & Compression Intelligence Engine for Jarvis.

Enables secure archive inspection, integrity testing, packing, and extraction
(ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ) with built-in Zip-Slip path-traversal
defense without external dependencies.
"""

from __future__ import annotations

import datetime
import json
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within


def _is_zip_slip_safe(target_dir: Path, path_to_extract: str) -> bool:
    """Ensure path to extract resolves strictly inside target_dir."""
    try:
        resolved_dst = (target_dir / path_to_extract).resolve()
        resolved_target = target_dir.resolve()
        return resolved_dst.is_relative_to(resolved_target)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Archive Operations
# --------------------------------------------------------------------------- #

def list_archive(path: Path) -> Dict[str, Any]:
    """List all entries in archive with metadata."""
    ext = path.name.lower()
    entries: List[Dict[str, Any]] = []
    total_uncompressed = 0
    total_compressed = 0

    if ext.endswith(".zip"):
        with zipfile.ZipFile(str(path), "r") as zf:
            for info in zf.infolist():
                dt = datetime.datetime(*info.date_time).strftime("%Y-%m-%d %H:%M:%S")
                total_uncompressed += info.file_size
                total_compressed += info.compress_size
                entries.append({
                    "filename": info.filename,
                    "size_bytes": info.file_size,
                    "compressed_bytes": info.compress_size,
                    "is_dir": info.is_dir(),
                    "date": dt,
                    "crc32": hex(info.CRC),
                })
    elif any(ext.endswith(s) for s in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        mode = "r:*"
        with tarfile.open(str(path), mode) as tf:
            for member in tf.getmembers():
                dt = datetime.datetime.fromtimestamp(member.mtime).strftime("%Y-%m-%d %H:%M:%S")
                total_uncompressed += member.size
                entries.append({
                    "filename": member.name,
                    "size_bytes": member.size,
                    "is_dir": member.isdir(),
                    "date": dt,
                })
        total_compressed = path.stat().st_size
    else:
        return {"error": f"Unsupported archive format for file: {path.name}"}

    ratio = round((1.0 - (total_compressed / total_uncompressed)) * 100, 1) if total_uncompressed else 0.0

    return {
        "archive": path.name,
        "total_files": len(entries),
        "uncompressed_bytes": total_uncompressed,
        "compressed_bytes": total_compressed,
        "compression_savings_pct": max(0.0, ratio),
        "entries": entries[:100],
    }


def test_archive(path: Path) -> Dict[str, Any]:
    """Test CRC32 and header integrity of an archive."""
    ext = path.name.lower()
    if ext.endswith(".zip"):
        with zipfile.ZipFile(str(path), "r") as zf:
            bad_file = zf.testzip()
            if bad_file:
                return {"status": "CORRUPT", "error": f"Corrupt file found in zip: {bad_file}"}
            return {"status": "OK", "archive": path.name, "message": "All CRC32 checksums verified."}
    elif any(ext.endswith(s) for s in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(str(path), "r:*") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    f = tf.extractfile(m)
                    if f:
                        f.read()  # Test reading entire stream
            return {"status": "OK", "archive": path.name, "message": "All TAR member streams verified."}
    return {"status": "ERROR", "error": "Unsupported archive format"}


def create_archive(src_path: Path, dst_path: Path, archive_format: str = "zip", level: int = 6,
                   selected_files: Optional[List[str]] = None) -> str:
    """Pack files or directory into archive.

    ``selected_files`` optionally restricts packing to those members (relative
    paths, either separator accepted); ``None`` packs everything — the default
    preserves the historical whole-directory behaviour.
    """
    fmt = archive_format.lower().strip()
    if not dst_path.suffix:
        dst_path = dst_path.with_suffix(".zip" if fmt == "zip" else f".{fmt}")

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    wanted = None
    if selected_files:
        wanted = {Path(s).as_posix() for s in selected_files}

    if fmt in ("zip", ".zip"):
        with zipfile.ZipFile(str(dst_path), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=max(0, min(9, level))) as zf:
            if src_path.is_file():
                zf.write(str(src_path), arcname=src_path.name)
                count = 1
            else:
                for root, dirs, files in os.walk(src_path):
                    for f in files:
                        p = Path(root) / f
                        rel = p.relative_to(src_path)
                        if wanted is not None and rel.as_posix() not in wanted:
                            continue
                        zf.write(str(p), arcname=str(rel))
                        count += 1
    else:
        mode = "w:gz" if "gz" in fmt else ("w:bz2" if "bz2" in fmt else ("w:xz" if "xz" in fmt else "w"))
        with tarfile.open(str(dst_path), mode) as tf:
            if src_path.is_file():
                tf.add(str(src_path), arcname=src_path.name)
                count = 1
            else:
                for root, dirs, files in os.walk(src_path):
                    for f in files:
                        p = Path(root) / f
                        rel = p.relative_to(src_path)
                        if wanted is not None and rel.as_posix() not in wanted:
                            continue
                        tf.add(str(p), arcname=str(rel))
                        count += 1

    size_kb = round(dst_path.stat().st_size / 1024.0, 1)
    return f"created archive {dst_path.name} with {count} file(s) ({size_kb} KB)"


def extract_archive(src_path: Path, dst_dir: Path, selected_files: Optional[List[str]] = None) -> str:
    """Extract archive with strict Zip-Slip path-traversal protection."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    ext = src_path.name.lower()
    extracted_count = 0

    if ext.endswith(".zip"):
        with zipfile.ZipFile(str(src_path), "r") as zf:
            for member in zf.namelist():
                if selected_files and member not in selected_files:
                    continue
                if not _is_zip_slip_safe(dst_dir, member):
                    return f"security violation: archive member '{member}' attempts path traversal outside destination"
                zf.extract(member, path=str(dst_dir))
                extracted_count += 1
    else:
        with tarfile.open(str(src_path), "r:*") as tf:
            for member in tf.getmembers():
                if selected_files and member.name not in selected_files:
                    continue
                if not _is_zip_slip_safe(dst_dir, member.name):
                    return f"security violation: archive member '{member.name}' attempts path traversal outside destination"
                try:
                    tf.extract(member, path=str(dst_dir), filter="data")
                except TypeError:
                    tf.extract(member, path=str(dst_dir))
                extracted_count += 1

    return f"extracted {extracted_count} file(s) to {dst_dir}"


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def archive_intel(
    op: str = "list",
    path: str = "",
    target: str = "",
    files: Optional[List[str]] = None,
    archive_format: str = "zip",
    level: int = 6,
    allow: tuple[str, ...] = (),
) -> str:
    """Manage archives with inspection, integrity testing, packing, and safe extraction.

    Operations:
      - 'list': List archive contents, sizes, timestamps, and compression ratio.
      - 'test': Verify archive integrity and CRC32 checksums.
      - 'create' / 'pack': Pack folder/file into ZIP, TAR.GZ, TAR.BZ2, or TAR.XZ.
      - 'extract' / 'unzip': Safely extract archive to target directory with Zip-Slip protection.
    """
    if not path:
        return "archive_intel requires a 'path' parameter"

    p = _expand(path)
    op_clean = (op or "list").strip().lower()

    if op_clean in ("list", "ls", "contents"):
        if not p.exists():
            return f"archive not found: {p}"
        res = list_archive(p)
        return json.dumps(res, indent=2)

    elif op_clean in ("test", "verify", "check"):
        if not p.exists():
            return f"archive not found: {p}"
        res = test_archive(p)
        return json.dumps(res, indent=2)

    elif op_clean in ("create", "pack", "zip", "compress"):
        if not p.exists():
            return f"source path not found: {p}"
        dst = _expand(target) if target else p.with_suffix(f".{archive_format.lstrip('.')}")
        return create_archive(p, dst, archive_format=archive_format, level=level,
                              selected_files=files)

    elif op_clean in ("extract", "unzip", "unpack", "untar"):
        if not p.exists():
            return f"archive not found: {p}"
        dst = _expand(target) if target else p.parent / p.stem
        return extract_archive(p, dst, selected_files=files)

    return f"unknown archive_intel op '{op}' - supported: list, test, create, extract"
