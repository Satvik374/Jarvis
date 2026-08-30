"""System Diagnostics and Self-Repair Health Engine for Jarvis.

Performs deep environmental, subsystem, database integrity, and API key
diagnostic checks, and executes automated self-healing maintenance.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config, load_config
from .files import _expand


@dataclass
class DiagnosticItem:
    subsystem: str
    status: str  # "OK", "WARN", "FAIL"
    message: str
    details: Optional[Dict[str, Any]] = None


def check_runtime() -> List[DiagnosticItem]:
    items: List[DiagnosticItem] = []
    # Python Version
    v = sys.version_info
    py_ver = f"{v.major}.{v.minor}.{v.micro}"
    if v.major >= 3 and v.minor >= 10:
        items.append(DiagnosticItem("Runtime", "OK", f"Python {py_ver} on {platform.system()} {platform.release()}"))
    else:
        items.append(DiagnosticItem("Runtime", "WARN", f"Python {py_ver} (Python 3.10+ recommended)"))

    # Optional Libraries Check
    optional_libs = {
        "pypdf": "PDF Document Reading",
        "pptx": "PowerPoint Presentation Parsing",
        "pygetwindow": "Desktop Window Management",
        "pyautogui": "Native Mouse & Keyboard Automation",
        "cv2": "Computer Vision / OpenCV Live Stream",
        "playwright": "Headless Browser Automation Engine",
        "sounddevice": "Audio Capture & Live Voice VAD",
        "yaml": "YAML Configuration & Serialization",
        "requests": "HTTP / REST API Connector Client",
    }

    for lib, purpose in optional_libs.items():
        try:
            __import__(lib)
            items.append(DiagnosticItem("Dependencies", "OK", f"{lib} available ({purpose})"))
        except ImportError:
            items.append(DiagnosticItem("Dependencies", "WARN", f"{lib} not installed ({purpose})"))

    return items


def check_brain_config(cfg: Config) -> List[DiagnosticItem]:
    items: List[DiagnosticItem] = []
    backend = cfg.brain.backend if hasattr(cfg, "brain") else "auto"
    items.append(DiagnosticItem("AI Brain", "OK", f"Configured Backend: {backend}"))

    from ..security import get_secret
    keys_found = []
    for k in ("JARVIS_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        if os.getenv(k, "").strip() or (get_secret(k) or "").strip():
            keys_found.append(k)

    if keys_found:
        items.append(DiagnosticItem("AI Brain", "OK", f"Active API Credentials: {', '.join(keys_found)}"))
    else:
        items.append(DiagnosticItem("AI Brain", "WARN", "No cloud LLM API keys found in .env or Credential Vault"))

    return items


def check_desktop() -> List[DiagnosticItem]:
    items: List[DiagnosticItem] = []
    try:
        from ..perception.screen import screen_size
        w, h = screen_size()
        items.append(DiagnosticItem("Desktop Display", "OK", f"Primary Screen Resolution: {w}x{h}"))
    except Exception as exc:
        items.append(DiagnosticItem("Desktop Display", "WARN", f"Could not determine screen resolution: {exc}"))

    try:
        from ..desktop import is_shadow_enabled
        shadow_on = is_shadow_enabled()
        items.append(DiagnosticItem("Shadow Desktop", "OK", f"Virtual Workspace Isolation: {'Enabled' if shadow_on else 'Disabled'}"))
    except Exception:
        pass

    return items


def check_browser_engine(cfg: Config) -> List[DiagnosticItem]:
    items: List[DiagnosticItem] = []
    try:
        from ..desktop.manager import ShadowDesktopManager
        exe = ShadowDesktopManager.find_browser_exe()
        if exe:
            items.append(DiagnosticItem("Browser Engine", "OK", f"Detected Browser Binary: {exe}"))
        else:
            items.append(DiagnosticItem("Browser Engine", "WARN", "No Chrome/Edge browser binary found in standard PATH"))
    except Exception as exc:
        items.append(DiagnosticItem("Browser Engine", "WARN", f"Browser check error: {exc}"))

    return items


def check_memory_db() -> List[DiagnosticItem]:
    items: List[DiagnosticItem] = []
    from ..memory.manager import get_default_db_path, get_memory_manager

    db_path = get_default_db_path()
    if not db_path.exists():
        items.append(DiagnosticItem("Memory DB", "OK", f"Database not yet initialized at {db_path.name} (auto-creates on first memory action)"))
        return items

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        res = cursor.fetchone()
        integrity_ok = res and res[0] == "ok"
        conn.close()

        if integrity_ok:
            mgr = get_memory_manager()
            stats = mgr.get_stats()
            items.append(DiagnosticItem(
                "Memory DB",
                "OK",
                f"SQLite Integrity Verified. Records: {stats.get('total_vectors', 0)} vectors, "
                f"{stats.get('graph_entities', 0)} entities, {stats.get('graph_relations', 0)} relations",
            ))
        else:
            items.append(DiagnosticItem("Memory DB", "FAIL", f"Database integrity check failed: {res}"))
    except Exception as exc:
        items.append(DiagnosticItem("Memory DB", "FAIL", f"Error checking database: {exc}"))

    return items


def check_security_vault() -> List[DiagnosticItem]:
    items: List[DiagnosticItem] = []
    try:
        from ..security.vault import get_credential_vault
        vault = get_credential_vault()
        stats = vault.get_stats()
        items.append(DiagnosticItem(
            "Security Vault",
            "OK",
            f"Fernet Encryption Active. Stored Secrets: {stats.get('total_secrets', 0)} entries",
        ))
    except Exception as exc:
        items.append(DiagnosticItem("Security Vault", "WARN", f"Vault check error: {exc}"))

    return items


def run_self_repair(cfg: Config) -> str:
    """Execute automated self-healing maintenance on databases and directories."""
    repairs: List[str] = []

    # 1. Vacuum & Reindex Memory Database
    from ..memory.manager import get_default_db_path
    db_path = get_default_db_path()
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM")
            conn.execute("REINDEX")
            conn.close()
            repairs.append(f"Optimized and reindexed SQLite memory database ({db_path.name})")
        except Exception as exc:
            repairs.append(f"Memory DB repair warning: {exc}")

    # 2. Vacuum Credential Vault Database
    try:
        from ..security.vault import get_credential_vault
        vault = get_credential_vault()
        v_conn = sqlite3.connect(str(vault.db_path))
        v_conn.execute("VACUUM")
        v_conn.execute("REINDEX")
        v_conn.close()
        repairs.append("Optimized and reindexed Credential Vault database")
    except Exception:
        pass

    # 3. Create missing standard project directories
    home = Path.home()
    for d in ("JarvisProjects", "Downloads", "Pictures"):
        target_dir = home / d
        if not target_dir.exists():
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                repairs.append(f"Created standard user folder ~/{d}")
            except Exception:
                pass

    return "Self-Repair Completed:\n" + "\n".join(f"  • {r}" for r in repairs)


def system_diagnostics(
    op: str = "health",
    subsystems: Optional[List[str]] = None,
    auto_fix: bool = False,
    cfg: Optional[Config] = None,
    allow: tuple[str, ...] = (),
) -> str:
    """Run environmental diagnostics and automated health inspection."""
    config = cfg or load_config()
    op_clean = (op or "health").strip().lower()

    if op_clean in ("repair", "fix", "optimize"):
        return run_self_repair(config)

    items: List[DiagnosticItem] = []
    items.extend(check_runtime())
    items.extend(check_brain_config(config))
    items.extend(check_desktop())
    items.extend(check_browser_engine(config))
    items.extend(check_memory_db())
    items.extend(check_security_vault())

    lines = [f"JARVIS System Health & Diagnostics Report ({len(items)} checks):"]
    for it in items:
        badge = "✅ [OK]  " if it.status == "OK" else ("⚠️ [WARN]" if it.status == "WARN" else "❌ [FAIL]")
        lines.append(f"  {badge} {it.subsystem:<16} : {it.message}")

    if auto_fix:
        repair_msg = run_self_repair(config)
        lines.append("\n" + repair_msg)

    return "\n".join(lines)
