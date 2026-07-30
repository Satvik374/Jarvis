"""Small, dependency-free LaTeX-to-terminal math renderer.

This is deliberately a readable Unicode linearizer, not a TeX layout engine.
It covers the notation language models commonly use in chat while preserving
unknown commands instead of silently discarding mathematical information.
"""

from __future__ import annotations

import re


# Values are ``(Unicode, ASCII fallback)``. Commands are tokenized exactly, so
# ``\subset`` can never corrupt the longer ``\subseteq``.
_SYMBOLS: dict[str, tuple[str, str]] = {
    "alpha": ("α", "alpha"), "beta": ("β", "beta"),
    "gamma": ("γ", "gamma"), "delta": ("δ", "delta"),
    "epsilon": ("ε", "epsilon"), "varepsilon": ("ϵ", "epsilon"),
    "zeta": ("ζ", "zeta"), "eta": ("η", "eta"),
    "theta": ("θ", "theta"), "vartheta": ("ϑ", "theta"),
    "iota": ("ι", "iota"), "kappa": ("κ", "kappa"),
    "lambda": ("λ", "lambda"), "mu": ("μ", "mu"),
    "nu": ("ν", "nu"), "xi": ("ξ", "xi"),
    "omicron": ("ο", "omicron"), "pi": ("π", "pi"),
    "varpi": ("ϖ", "pi"), "rho": ("ρ", "rho"),
    "sigma": ("σ", "sigma"), "varsigma": ("ς", "sigma"),
    "tau": ("τ", "tau"), "upsilon": ("υ", "upsilon"),
    "phi": ("φ", "phi"), "varphi": ("ϕ", "phi"),
    "chi": ("χ", "chi"), "psi": ("ψ", "psi"),
    "omega": ("ω", "omega"),
    "Gamma": ("Γ", "Gamma"), "Delta": ("Δ", "Delta"),
    "Theta": ("Θ", "Theta"), "Lambda": ("Λ", "Lambda"),
    "Xi": ("Ξ", "Xi"), "Pi": ("Π", "Pi"),
    "Sigma": ("Σ", "Sigma"), "Upsilon": ("Υ", "Upsilon"),
    "Phi": ("Φ", "Phi"), "Psi": ("Ψ", "Psi"),
    "Omega": ("Ω", "Omega"),
    "times": ("×", "*"), "cdot": ("·", "*"), "div": ("÷", "/"),
    "pm": ("±", "+/-"), "mp": ("∓", "-/+"),
    "le": ("≤", "<="), "leq": ("≤", "<="),
    "ge": ("≥", ">="), "geq": ("≥", ">="),
    "ne": ("≠", "!="), "neq": ("≠", "!="),
    "approx": ("≈", "~="), "equiv": ("≡", "==="),
    "sim": ("∼", "~"), "propto": ("∝", "proportional to"),
    "infty": ("∞", "infinity"), "partial": ("∂", "d"),
    "nabla": ("∇", "grad"), "sum": ("∑", "sum"),
    "prod": ("∏", "product"), "int": ("∫", "integral"),
    "iint": ("∬", "double integral"), "iiint": ("∭", "triple integral"),
    "oint": ("∮", "contour integral"),
    "rightarrow": ("→", "->"), "to": ("→", "->"),
    "leftarrow": ("←", "<-"), "leftrightarrow": ("↔", "<->"),
    "Rightarrow": ("⇒", "=>"), "Leftarrow": ("⇐", "<="),
    "Leftrightarrow": ("⇔", "<=>"), "implies": ("⇒", "=>"),
    "forall": ("∀", "for all"), "exists": ("∃", "exists"),
    "neg": ("¬", "not "), "land": ("∧", "and"), "lor": ("∨", "or"),
    "in": ("∈", "in"), "notin": ("∉", "not in"),
    "subset": ("⊂", "subset"), "subseteq": ("⊆", "subset or equal"),
    "supset": ("⊃", "superset"), "supseteq": ("⊇", "superset or equal"),
    "cup": ("∪", "union"), "cap": ("∩", "intersection"),
    "emptyset": ("∅", "empty set"),
    "ldots": ("…", "..."), "cdots": ("⋯", "..."),
    "langle": ("⟨", "<"), "rangle": ("⟩", ">"),
    "lvert": ("|", "|"), "rvert": ("|", "|"),
    "Vert": ("‖", "||"), "vert": ("|", "|"),
}

_NAMED_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
    "log", "ln", "exp", "lim", "min", "max", "det", "gcd",
}
_UNWRAP_COMMANDS = {
    "text", "textrm", "mathrm", "mathbf", "mathit", "mathsf", "mathtt",
    "operatorname", "overline", "underline",
}
_ACCENTS = {
    "vec": ("\u20d7", "vec"), "hat": ("\u0302", "hat"),
    "bar": ("\u0304", "bar"), "dot": ("\u0307", "dot"),
}
_IGNORED_COMMANDS = {"left", "right", "displaystyle", "textstyle"}
_SPACING_COMMANDS = {"quad": "  ", "qquad": "    ", "enspace": " "}

_SUPERSCRIPT = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ",
    "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ",
    "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ", "o": "ᵒ",
    "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}
_SUBSCRIPT = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ",
    "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ",
    "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ",
    "v": "ᵥ", "x": "ₓ",
}
_ENV_RE = re.compile(
    r"\\(?:begin|end)\{(?:align\*?|aligned|equation\*?|gather\*?|cases)\}"
)


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _group(text: str, pos: int, opening: str = "{",
           closing: str = "}") -> tuple[str, int] | None:
    """Read one balanced group and return ``(inside, position_after)``."""
    pos = _skip_space(text, pos)
    if pos >= len(text) or text[pos] != opening:
        return None
    depth = 1
    start = pos + 1
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return None


def _script(text: str, table: dict[str, str], marker: str,
            ascii_only: bool) -> str:
    if ascii_only:
        return marker + (f"({text})" if len(text) > 1 else text)
    converted = "".join(table.get(ch, "") for ch in text)
    if len(converted) == len(text):
        return converted
    return marker + (f"({text})" if len(text) > 1 else text)


def _fraction_part(text: str) -> str:
    text = text.strip()
    if re.fullmatch(r"[\w.]+", text, flags=re.UNICODE):
        return text
    return f"({text})"


def _render(text: str, ascii_only: bool, depth: int) -> str:
    if depth > 12:
        return text
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]

        if ch in "^_":
            table = _SUPERSCRIPT if ch == "^" else _SUBSCRIPT
            marker = ch
            grouped = _group(text, i + 1)
            if grouped is not None:
                raw, i = grouped
                value = _render(raw, ascii_only, depth + 1)
                out.append(_script(value, table, marker, ascii_only))
                continue
            if i + 1 < len(text):
                value = _render(text[i + 1], ascii_only, depth + 1)
                out.append(_script(value, table, marker, ascii_only))
                i += 2
                continue

        if ch != "\\":
            if ch not in "{}":                 # TeX grouping is not visible
                out.append(" " if ch == "~" else ch)
            i += 1
            continue

        if i + 1 >= len(text):
            out.append("\\")
            break
        nxt = text[i + 1]
        if nxt in "{}_$%#&":
            out.append(nxt)
            i += 2
            continue
        if nxt in ",;:":
            out.append(" ")
            i += 2
            continue
        if nxt == "!":
            i += 2
            continue
        if nxt in ("\\", " "):
            out.append(" ")
            i += 2
            continue

        match = re.match(r"\\([A-Za-z]+)", text[i:])
        if match is None:
            out.append("\\")
            i += 1
            continue
        name = match.group(1)
        after = i + len(match.group(0))

        if name in {"frac", "dfrac", "tfrac"}:
            numerator = _group(text, after)
            denominator = (_group(text, numerator[1])
                           if numerator is not None else None)
            if numerator is not None and denominator is not None:
                top = _render(numerator[0], ascii_only, depth + 1)
                bottom = _render(denominator[0], ascii_only, depth + 1)
                slash = "/" if ascii_only else "⁄"
                out.append(
                    _fraction_part(top) + slash + _fraction_part(bottom)
                )
                i = denominator[1]
                continue

        if name == "sqrt":
            pos = _skip_space(text, after)
            index = ""
            if pos < len(text) and text[pos] == "[":
                indexed = _group(text, pos, "[", "]")
                if indexed is not None:
                    index, pos = indexed
            radicand = _group(text, pos)
            if radicand is not None:
                value = _render(radicand[0], ascii_only, depth + 1)
                if ascii_only:
                    prefix = f"root[{index}]" if index else "sqrt"
                else:
                    prefix = (_script(index, _SUPERSCRIPT, "^", False)
                              if index else "") + "√"
                out.append(f"{prefix}({value})")
                i = radicand[1]
                continue

        if name in _UNWRAP_COMMANDS:
            grouped = _group(text, after)
            if grouped is not None:
                out.append(_render(grouped[0], ascii_only, depth + 1))
                i = grouped[1]
                continue

        if name in _ACCENTS:
            grouped = _group(text, after)
            if grouped is not None:
                value = _render(grouped[0], ascii_only, depth + 1)
                mark, word = _ACCENTS[name]
                out.append(f"{word}({value})" if ascii_only else value + mark)
                i = grouped[1]
                continue

        if name in _SYMBOLS:
            out.append(_SYMBOLS[name][1 if ascii_only else 0])
        elif name in _NAMED_FUNCTIONS:
            out.append(name)
        elif name in _SPACING_COMMANDS:
            out.append(_SPACING_COMMANDS[name])
        elif name not in _IGNORED_COMMANDS:
            # Unknown TeX is kept visible. Losing a symbol is worse than
            # showing the user the command that this small renderer lacks.
            out.append("\\" + name)
        i = after

    return "".join(out)


def render_latex(math: str, *, ascii_only: bool = False) -> str:
    """Convert common LaTeX math to readable one-line terminal text."""
    text = str(math).strip()
    if text.startswith("$$") and text.endswith("$$") and len(text) >= 4:
        text = text[2:-2]
    elif text.startswith("$") and text.endswith("$") and len(text) >= 2:
        text = text[1:-1]
    rendered = _render(text, ascii_only, 0)
    return re.sub(r"[ \t]+", " ", rendered).strip()


def display_latex_lines(math: str, *, ascii_only: bool = False) -> list[str]:
    """Render a display-math block, including common aligned environments."""
    text = _ENV_RE.sub("", str(math))
    text = text.replace(r"\\", "\n")
    lines: list[str] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        # Alignment markers are layout hints in TeX. Two spaces remain useful
        # for matrices/cases in a linear terminal representation.
        raw = raw.replace("&", "  ")
        lines.append(render_latex(raw, ascii_only=ascii_only))
    return lines or [""]


def looks_like_math(candidate: str) -> bool:
    """Conservative dollar-pair heuristic that avoids currency and shells."""
    value = candidate.strip()
    if not value or "\n" in value:
        return False
    if re.search(r"\\[A-Za-z]+|[\^_={}+\-*/<>()\[\]]", value):
        return True
    if re.search(r"\d[A-Za-z]|[A-Za-z]\d", value):
        return True
    if re.fullmatch(r"(?:\d+(?:\.\d+)?|[A-Za-z]{1,3}|[Α-ω])", value):
        return True
    return False
