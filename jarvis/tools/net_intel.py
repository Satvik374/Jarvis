"""Network, Port & SSL Diagnostic Engine for Jarvis.

Enables TCP port reachability checks, latency measurement, DNS resolution,
TLS/SSL certificate introspection, and local listening socket inspection
using Python standard library without external dependencies.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import ssl
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def check_tcp_port(host: str, port: int, timeout: float = 3.0) -> Dict[str, Any]:
    """Test TCP socket reachability and measure round-trip connection latency."""
    h = host.strip()
    p = int(port)
    start_t = time.perf_counter()
    try:
        sock = socket.create_connection((h, p), timeout=max(0.5, timeout))
        elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
        peer = sock.getpeername()
        sock.close()
        return {
            "status": "OPEN",
            "host": h,
            "port": p,
            "latency_ms": elapsed_ms,
            "peer_ip": peer[0] if peer else h,
        }
    except socket.timeout:
        return {"status": "TIMEOUT", "host": h, "port": p, "error": f"Connection timed out after {timeout}s"}
    except ConnectionRefusedError:
        return {"status": "CLOSED", "host": h, "port": p, "error": "Connection refused by host"}
    except Exception as exc:
        return {"status": "ERROR", "host": h, "port": p, "error": str(exc)}


def resolve_dns(host: str) -> Dict[str, Any]:
    """Resolve DNS hostname to IPv4 and IPv6 addresses."""
    h = host.strip()
    try:
        # Resolve all addresses
        addr_info = socket.getaddrinfo(h, None)
        ipv4_addrs = list(dict.fromkeys(ai[4][0] for ai in addr_info if ai[0] == socket.AF_INET))
        ipv6_addrs = list(dict.fromkeys(ai[4][0] for ai in addr_info if ai[0] == socket.AF_INET6))
        canonical = addr_info[0][3] if addr_info and len(addr_info[0]) > 3 else h

        return {
            "host": h,
            "canonical_name": canonical or h,
            "ipv4": ipv4_addrs,
            "ipv6": ipv6_addrs,
            "status": "OK",
        }
    except Exception as exc:
        return {"host": h, "status": "ERROR", "error": f"DNS resolution failed: {exc}"}


def inspect_ssl_cert(host: str, port: int = 443, timeout: float = 5.0) -> Dict[str, Any]:
    """Connect via TLS and inspect SSL certificate validity, issuer, and SANs."""
    h = host.strip()
    p = int(port or 443)
    ctx = ssl.create_default_context()

    try:
        with socket.create_connection((h, p), timeout=max(1.0, timeout)) as sock:
            with ctx.wrap_socket(sock, server_hostname=h) as ssock:
                cert = ssock.getpeercert()
                cipher = ssock.cipher()
                tls_ver = ssock.version()

                # Parse subject & issuer
                subject_dict = dict(x[0] for x in cert.get("subject", ()))
                issuer_dict = dict(x[0] for x in cert.get("issuer", ()))
                not_before = cert.get("notBefore", "")
                not_after = cert.get("notAfter", "")
                sans = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]

                # Calculate days to expiry
                days_left = None
                if not_after:
                    try:
                        exp_dt = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
                        days_left = (exp_dt - datetime.datetime.now(datetime.timezone.utc)).days
                    except Exception:
                        pass

                return {
                    "host": h,
                    "port": p,
                    "status": "VALID",
                    "tls_version": tls_ver,
                    "cipher": cipher[0] if cipher else "",
                    "subject": subject_dict.get("commonName", ""),
                    "issuer": issuer_dict.get("organizationName", issuer_dict.get("commonName", "")),
                    "valid_from": not_before,
                    "valid_to": not_after,
                    "days_remaining": days_left,
                    "sans": sans[:10],
                }
    except ssl.SSLCertVerificationError as exc:
        return {"host": h, "port": p, "status": "INVALID", "error": f"SSL verification failed: {exc.verify_message}"}
    except Exception as exc:
        return {"host": h, "port": p, "status": "ERROR", "error": str(exc)}


def list_listening_ports() -> str:
    """Enumerate local active listening TCP ports."""
    try:
        if os.name == "nt":
            proc = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10)
            lines = proc.stdout.splitlines()
            listening = []
            for l in lines:
                if "LISTENING" in l:
                    parts = l.split()
                    if len(parts) >= 5:
                        local_addr = parts[1]
                        pid = parts[4]
                        listening.append(f"  • {local_addr:<25} (PID {pid})")
            if not listening:
                return "no listening TCP ports found"
            return f"Active Listening TCP Ports ({len(listening)}):\n" + "\n".join(listening[:40])
        else:
            proc = subprocess.run(["netstat", "-tuln"], capture_output=True, text=True, timeout=10)
            return proc.stdout[:3000]
    except Exception as exc:
        return f"failed to query listening ports: {exc}"


def net_intel(
    op: str = "port_check",
    host: str = "127.0.0.1",
    port: int = 80,
    timeout: int = 3,
    allow: tuple[str, ...] = (),
) -> str:
    """Execute network diagnostics, port probing, DNS lookup, or SSL certificate checks.

    Operations:
      - 'port_check' / 'connect': Probe TCP reachability to host:port.
      - 'dns' / 'resolve': Resolve DNS hostnames to IPv4/IPv6.
      - 'ssl' / 'cert': Inspect TLS/SSL certificate chain and expiration.
      - 'listening' / 'ports': Enumerate local listening TCP ports.
    """
    op_clean = (op or "port_check").strip().lower()

    if op_clean in ("port_check", "connect", "probe", "port"):
        if not host:
            return "port_check requires a 'host'"
        res = check_tcp_port(host, int(port or 80), timeout=float(timeout or 3))
        return json.dumps(res, indent=2)

    elif op_clean in ("dns", "resolve", "lookup"):
        if not host:
            return "dns lookup requires a 'host'"
        res = resolve_dns(host)
        return json.dumps(res, indent=2)

    elif op_clean in ("ssl", "cert", "certificate", "tls"):
        if not host:
            return "ssl inspection requires a 'host'"
        res = inspect_ssl_cert(host, int(port or 443), timeout=float(timeout or 5))
        return json.dumps(res, indent=2)

    elif op_clean in ("listening", "ports", "netstat", "open_ports"):
        return list_listening_ports()

    return f"unknown net_intel op '{op}' - supported: port_check, dns, ssl, listening"
