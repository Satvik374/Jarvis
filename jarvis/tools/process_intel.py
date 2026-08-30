"""Process Resource Monitor & Inspector Engine for Jarvis.

Enables inspecting running processes, identifying top CPU/memory consumers,
searching background daemons, and safely terminating runaway processes with
built-in OS kernel and critical service protection.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_PROTECTED_NAMES: Set[str] = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "init",
    "systemd",
    "kthreadd",
}


def _run_ps_cmd(script: str, timeout: int = 10) -> Tuple[int, str, str]:
    """Run PowerShell command with UTF-8 encoding."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return -1, "", str(exc)


# --------------------------------------------------------------------------- #
# Process Querying
# --------------------------------------------------------------------------- #

def get_top_processes(sort_by: str = "memory", limit: int = 20) -> Dict[str, Any]:
    """List top processes sorted by working set memory or CPU."""
    lim = max(1, min(100, limit))
    sort_prop = "WorkingSet64" if sort_by.lower().startswith("mem") else "CPU"

    if os.name == "nt":
        script = (
            f"Get-Process | Where-Object {{ $_.Id -ne 0 }} | "
            f"Sort-Object -Descending {sort_prop} | Select-Object -First {lim} "
            f"Id, ProcessName, @{{Name='MemoryMB';Expression={{[math]::Round($_.WorkingSet64 / 1MB, 1)}}}}, "
            f"@{{Name='CPU';Expression={{[math]::Round($_.CPU, 1)}}}}, Path | "
            f"ConvertTo-Json -Compress"
        )
        code, out, err = _run_ps_cmd(script)
        if code != 0 or not out:
            return {"error": f"Failed to list processes: {err or out}"}

        try:
            data = json.loads(out)
            items = data if isinstance(data, list) else [data]
            processes = []
            for item in items:
                processes.append({
                    "pid": item.get("Id"),
                    "name": item.get("ProcessName", ""),
                    "memory_mb": item.get("MemoryMB", 0.0),
                    "cpu_sec": item.get("CPU", 0.0),
                    "path": item.get("Path") or "",
                })
            return {
                "sorted_by": sort_by,
                "count": len(processes),
                "processes": processes,
            }
        except Exception as exc:
            return {"error": f"Failed to parse process data: {exc}"}
    else:
        try:
            proc = subprocess.run(["ps", "aux", "--sort=-%mem"], capture_output=True, text=True, timeout=10)
            return {"raw_ps": proc.stdout[:3000]}
        except Exception as exc:
            return {"error": str(exc)}


def inspect_pid(pid: int) -> Dict[str, Any]:
    """Inspect detailed metadata for a specific PID."""
    if os.name == "nt":
        script = (
            f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
            f"if ($p) {{ "
            f"  [PSCustomObject]@{{ "
            f"    Id = $p.Id; "
            f"    Name = $p.ProcessName; "
            f"    Path = $p.Path; "
            f"    MemoryMB = [math]::Round($p.WorkingSet64 / 1MB, 2); "
            f"    VirtualMB = [math]::Round($p.VirtualMemorySize64 / 1MB, 2); "
            f"    CPU_Sec = [math]::Round($p.CPU, 2); "
            f"    Threads = $p.Threads.Count; "
            f"    Responding = $p.Responding; "
            f"    StartTime = if ($p.StartTime) {{ $p.StartTime.ToString('yyyy-MM-dd HH:mm:ss') }} else {{ '' }}; "
            f"  }} | ConvertTo-Json -Compress "
            f"}}"
        )
        code, out, err = _run_ps_cmd(script)
        if code != 0 or not out:
            return {"error": f"Process PID {pid} not found or access denied: {err or out}"}
        try:
            return json.loads(out)
        except Exception as exc:
            return {"error": str(exc)}
    else:
        return {"pid": pid, "os": os.name}


def find_processes(pattern: str) -> Dict[str, Any]:
    """Search active processes by name substring."""
    pat = pattern.strip().lower()
    if not pat:
        return {"error": "Search pattern cannot be empty"}

    if os.name == "nt":
        script = (
            f"Get-Process | Where-Object {{ $_.ProcessName -like '*{pat}*' }} | "
            f"Select-Object -First 30 "
            f"Id, ProcessName, @{{Name='MemoryMB';Expression={{[math]::Round($_.WorkingSet64 / 1MB, 1)}}}}, "
            f"@{{Name='CPU';Expression={{[math]::Round($_.CPU, 1)}}}}, Path | "
            f"ConvertTo-Json -Compress"
        )
        code, out, err = _run_ps_cmd(script)
        if code != 0 or not out:
            return {"pattern": pat, "count": 0, "matches": []}
        try:
            data = json.loads(out)
            items = data if isinstance(data, list) else [data]
            matches = []
            for item in items:
                matches.append({
                    "pid": item.get("Id"),
                    "name": item.get("ProcessName", ""),
                    "memory_mb": item.get("MemoryMB", 0.0),
                    "cpu_sec": item.get("CPU", 0.0),
                    "path": item.get("Path") or "",
                })
            return {"pattern": pat, "count": len(matches), "matches": matches}
        except Exception:
            return {"pattern": pat, "count": 0, "matches": []}
    return {"pattern": pat, "matches": []}


# --------------------------------------------------------------------------- #
# Process Termination
# --------------------------------------------------------------------------- #

def terminate_process(pid: Optional[int] = None, name: Optional[str] = None, force: bool = False) -> str:
    """Terminate a process or process tree by PID or Name with safety checks."""
    if pid is not None:
        p_id = int(pid)
        if p_id in (0, 4):
            return f"refused: PID {p_id} is a critical OS kernel process"

        # Check process name before terminating
        info = inspect_pid(p_id)
        p_name = str(info.get("Name", "")).lower()
        if p_name in _PROTECTED_NAMES or f"{p_name}.exe" in _PROTECTED_NAMES:
            return f"refused: cannot terminate protected system process '{p_name}' (PID {p_id})"

        try:
            if os.name == "nt":
                flag = "/F" if force else ""
                proc = subprocess.run(["taskkill", "/PID", str(p_id), "/T", flag], capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    return f"successfully terminated process tree for PID {p_id}"
                return f"taskkill output: {proc.stdout.strip() or proc.stderr.strip()}"
            else:
                os.kill(p_id, signal.SIGKILL if force else signal.SIGTERM)
                return f"terminated process PID {p_id}"
        except Exception as exc:
            return f"failed to terminate PID {p_id}: {exc}"

    elif name:
        clean_name = name.strip().lower()
        if clean_name in _PROTECTED_NAMES or f"{clean_name}.exe" in _PROTECTED_NAMES:
            return f"refused: cannot terminate protected system process '{name}'"

        try:
            if os.name == "nt":
                img_name = clean_name if clean_name.endswith(".exe") else f"{clean_name}.exe"
                flag = "/F" if force else ""
                proc = subprocess.run(["taskkill", "/IM", img_name, "/T", flag], capture_output=True, text=True, timeout=10)
                return f"taskkill output: {proc.stdout.strip() or proc.stderr.strip()}"
            else:
                subprocess.run(["pkill", clean_name], timeout=5)
                return f"sent termination signal to '{name}'"
        except Exception as exc:
            return f"failed to terminate '{name}': {exc}"

    return "terminate requires either 'pid' or 'name'"


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def process_intel(
    op: str = "list",
    pid: Optional[int] = None,
    name: str = "",
    sort_by: str = "memory",
    limit: int = 20,
    force: bool = False,
    allow: tuple[str, ...] = (),
) -> str:
    """Monitor, inspect, search, and manage system processes and resource usage.

    Operations:
      - 'list' / 'top': List processes sorted by memory or CPU usage.
      - 'inspect' / 'info': Detailed metadata (memory, threads, start time) for specific PID.
      - 'find' / 'search': Search active processes by name substring.
      - 'terminate' / 'kill': Safely kill a process or process tree with OS protection.
    """
    op_clean = (op or "list").strip().lower()

    if op_clean in ("list", "top", "ps"):
        res = get_top_processes(sort_by=sort_by, limit=int(limit or 20))
        return json.dumps(res, indent=2)

    elif op_clean in ("inspect", "info", "pid"):
        target_pid = pid if pid is not None else (int(name) if name.isdigit() else os.getpid())
        res = inspect_pid(int(target_pid))
        return json.dumps(res, indent=2)

    elif op_clean in ("find", "search", "query"):
        pat = name or (str(pid) if pid is not None else "")
        res = find_processes(pat)
        return json.dumps(res, indent=2)

    elif op_clean in ("terminate", "kill", "stop"):
        return terminate_process(pid=pid, name=name, force=force)

    return f"unknown process_intel op '{op}' - supported: list, inspect, find, terminate"
