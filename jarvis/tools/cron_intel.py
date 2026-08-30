"""Cron Schedule & Timezone Intelligence Engine for Jarvis.

Enables cron expression validation, human-readable schedule explanations,
upcoming occurrence generation, and timezone-aware schedule calculations.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

_DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


# --------------------------------------------------------------------------- #
# Cron Parsing & Validation
# --------------------------------------------------------------------------- #

def _parse_part(part: str, min_val: int, max_val: int) -> Tuple[Set[int], Optional[str]]:
    """Parse a single cron field into matching integer values."""
    part = part.strip()
    if not part:
        return set(), "empty field"

    values: Set[int] = set()

    for sub in part.split(","):
        sub = sub.strip()
        if not sub:
            continue

        step = 1
        if "/" in sub:
            range_part, step_str = sub.split("/", 1)
            try:
                step = int(step_str)
                if step <= 0:
                    return set(), f"invalid step value '{step_str}'"
            except ValueError:
                return set(), f"invalid step value '{step_str}'"
        else:
            range_part = sub

        if range_part == "*":
            start, end = min_val, max_val
        elif "-" in range_part:
            s_str, e_str = range_part.split("-", 1)
            try:
                start, end = int(s_str), int(e_str)
            except ValueError:
                return set(), f"invalid range '{range_part}'"
        else:
            try:
                start = end = int(range_part)
            except ValueError:
                return set(), f"invalid value '{range_part}'"

        if start < min_val or end > max_val or start > end:
            return set(), f"value out of range [{min_val}..{max_val}]: '{range_part}'"

        for val in range(start, end + 1, step):
            values.add(val)

    return values, None


def validate_cron(expr: str) -> Dict[str, Any]:
    """Validate 5-part cron expression and return parsed field sets."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return {
            "valid": False,
            "error": f"Cron expression must have exactly 5 parts (got {len(parts)}): '{expr}'",
            "expression": expr,
        }

    m_str, h_str, dom_str, mon_str, dow_str = parts

    mins, err_m = _parse_part(m_str, 0, 59)
    if err_m:
        return {"valid": False, "error": f"Minute error: {err_m}", "expression": expr}

    hours, err_h = _parse_part(h_str, 0, 23)
    if err_h:
        return {"valid": False, "error": f"Hour error: {err_h}", "expression": expr}

    doms, err_dom = _parse_part(dom_str, 1, 31)
    if err_dom:
        return {"valid": False, "error": f"Day-of-month error: {err_dom}", "expression": expr}

    mons, err_mon = _parse_part(mon_str, 1, 12)
    if err_mon:
        return {"valid": False, "error": f"Month error: {err_mon}", "expression": expr}

    dows, err_dow = _parse_part(dow_str, 0, 7)
    if err_dow:
        return {"valid": False, "error": f"Day-of-week error: {err_dow}", "expression": expr}

    # Normalize 7 to 0 (both represent Sunday)
    if 7 in dows:
        dows.remove(7)
        dows.add(0)

    return {
        "valid": True,
        "expression": expr,
        "fields": {
            "minute": m_str,
            "hour": h_str,
            "day_of_month": dom_str,
            "month": mon_str,
            "day_of_week": dow_str,
        },
        "_values": {
            "minutes": mins,
            "hours": hours,
            "doms": doms,
            "months": mons,
            "dows": dows,
        },
    }


# --------------------------------------------------------------------------- #
# Human-Readable Explanation
# --------------------------------------------------------------------------- #

def explain_cron(expr: str) -> str:
    """Translate standard 5-part cron syntax into clear natural language."""
    v = validate_cron(expr)
    if not v["valid"]:
        return f"Invalid cron expression: {v['error']}"

    parts = expr.strip().split()
    m_str, h_str, dom_str, mon_str, dow_str = parts

    desc_parts = []

    # Minutes
    if m_str == "*":
        desc_parts.append("Every minute")
    elif m_str.startswith("*/"):
        desc_parts.append(f"Every {m_str[2:]} minutes")
    else:
        desc_parts.append(f"At minute {m_str}")

    # Hours
    if h_str == "*":
        pass
    elif h_str.startswith("*/"):
        desc_parts.append(f"every {h_str[2:]} hours")
    elif "-" in h_str:
        desc_parts.append(f"between hour {h_str}")
    else:
        desc_parts.append(f"at {int(h_str):02d}:00")

    # Day of Month
    if dom_str != "*":
        desc_parts.append(f"on day-of-month {dom_str}")

    # Month
    if mon_str != "*":
        if mon_str.isdigit() and 1 <= int(mon_str) <= 12:
            desc_parts.append(f"in {_MONTH_NAMES[int(mon_str)]}")
        else:
            desc_parts.append(f"in month {mon_str}")

    # Day of Week
    if dow_str != "*":
        if dow_str == "1-5":
            desc_parts.append("Monday through Friday")
        elif dow_str == "0,6" or dow_str == "6,0":
            desc_parts.append("on weekends")
        elif dow_str.isdigit() and int(dow_str) < len(_DOW_NAMES):
            desc_parts.append(f"on {_DOW_NAMES[int(dow_str)]}")
        else:
            desc_parts.append(f"on day-of-week {dow_str}")

    return ", ".join(desc_parts)


# --------------------------------------------------------------------------- #
# Next Occurrences Generator
# --------------------------------------------------------------------------- #

def get_next_runs(
    expr: str,
    count: int = 5,
    tz_name: str = "UTC",
    base_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the next N occurrence timestamps starting from base time."""
    v = validate_cron(expr)
    if not v["valid"]:
        return {"error": v["error"]}

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
        tz_name = "UTC"

    if base_time:
        try:
            start_dt = datetime.fromisoformat(base_time)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=tz)
        except Exception:
            start_dt = datetime.now(tz)
    else:
        start_dt = datetime.now(tz)

    val_dict = v["_values"]
    mins: Set[int] = val_dict["minutes"]
    hours: Set[int] = val_dict["hours"]
    doms: Set[int] = val_dict["doms"]
    mons: Set[int] = val_dict["months"]
    dows: Set[int] = val_dict["dows"]

    occurrences: List[str] = []
    curr = start_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_steps = 500000  # Safety guard (~1 year search window)
    step = 0

    while len(occurrences) < count and step < max_steps:
        step += 1
        # Check Month
        if curr.month not in mons:
            # Advance to first day of next month
            if curr.month == 12:
                curr = curr.replace(year=curr.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                curr = curr.replace(month=curr.month + 1, day=1, hour=0, minute=0)
            continue

        # Check Day of Month & Day of Week
        # Python weekday: Monday=0..Sunday=6. Convert to Cron: Sunday=0..Saturday=6
        cron_dow = (curr.weekday() + 1) % 7
        if curr.day not in doms or cron_dow not in dows:
            curr = (curr + timedelta(days=1)).replace(hour=0, minute=0)
            continue

        # Check Hour
        if curr.hour not in hours:
            curr = (curr + timedelta(hours=1)).replace(minute=0)
            continue

        # Check Minute
        if curr.minute not in mins:
            curr += timedelta(minutes=1)
            continue

        occurrences.append(curr.isoformat())
        curr += timedelta(minutes=1)

    return {
        "expression": expr,
        "timezone": tz_name,
        "explanation": explain_cron(expr),
        "count": len(occurrences),
        "next_runs": occurrences,
    }


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def cron_intel(
    op: str = "explain",
    expr: str = "* * * * *",
    count: int = 5,
    timezone_name: str = "UTC",
    base_time: str = "",
    allow: tuple[str, ...] = (),
) -> str:
    """Cron Schedule & Timezone Intelligence Engine.

    Operations:
      - 'explain' / 'describe': Translate cron expression into natural English text.
      - 'next' / 'runs': Calculate the next N occurrence timestamps.
      - 'validate' / 'check': Validate cron syntax and fields.
    """
    op_clean = (op or "explain").strip().lower()
    clean_expr = expr.strip() or "* * * * *"

    if op_clean in ("explain", "describe", "human"):
        v = validate_cron(clean_expr)
        if not v["valid"]:
            return json.dumps(v, indent=2)
        return json.dumps({
            "expression": clean_expr,
            "explanation": explain_cron(clean_expr),
            "fields": v.get("fields"),
        }, indent=2)

    elif op_clean in ("next", "runs", "schedule", "occurrences"):
        res = get_next_runs(
            expr=clean_expr,
            count=int(count or 5),
            tz_name=timezone_name or "UTC",
            base_time=base_time,
        )
        return json.dumps(res, indent=2)

    elif op_clean in ("validate", "check"):
        res = validate_cron(clean_expr)
        res.pop("_values", None)
        return json.dumps(res, indent=2)

    return f"unknown cron_intel op '{op}' - supported: explain, next, validate"
