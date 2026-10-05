"""Inspect web/circuit.bin -- a small reference reader for the container."""
from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
PATH = HERE / "web" / "circuit.bin"

NUMPY = {"float32": "<f4", "float64": "<f8", "int32": "<i4", "uint32": "<u4",
         "int8": "i1", "uint8": "u1", "int64": "<i8", "uint16": "<u2"}


def read(path: Path):
    raw = path.read_bytes()
    if raw[:8] != b"FLYBRN01":
        raise ValueError("not a FLYBRN01 container")
    o = 8
    (n,) = struct.unpack_from("<I", raw, o); o += 4
    meta = json.loads(raw[o:o + n]); o += n
    (nb,) = struct.unpack_from("<I", raw, o); o += 4
    arrays = {}
    for _ in range(nb):
        (nl,) = struct.unpack_from("<I", raw, o); o += 4
        name = raw[o:o + nl].decode(); o += nl
        dtype_code, ndim = struct.unpack_from("<BB", raw, o); o += 2
        shape = struct.unpack_from("<" + "I" * ndim, raw, o); o += 4 * ndim
        (bl,) = struct.unpack_from("<I", raw, o); o += 4
        name_of = meta["blocks"][name]["dtype"]
        arrays[name] = np.frombuffer(raw[o:o + bl], dtype=np.dtype(NUMPY[name_of])).reshape(shape)
        o += bl
    return meta, arrays


def main() -> None:
    meta, a = read(PATH)
    print("blocks:")
    for k, v in a.items():
        print(f"  {k:16s} {str(v.dtype):9s} {v.shape}")
    ct = np.array(meta["cell_types"])
    types = ct[a["ctid"].astype(int)]
    pre, post, w = a["pre"].astype(int), a["post"].astype(int), a["w"]
    is_r = np.isin(types, ["R1-6", "R7", "R8"])

    print(f"\nneurons {meta['n_neurons']:,}   edges {meta['n_edges']:,}   "
          f"synapses {meta['n_synapses']:,}")
    print(f"photoreceptors {int(is_r.sum()):,}")

    from_photo = is_r[pre]
    print(f"edges out of photoreceptors: {int(from_photo.sum()):,}  "
          f"((w[from_photo] > 0).sum() exc / {(w[from_photo] < 0).sum()} inh)")
    print("  top postsynaptic targets:",
          Counter(types[post[from_photo]]).most_common(6))
    print("  predicted transmitter of photoreceptors:",
          Counter(np.array(meta["nt"])[is_r]).most_common())

    for t in ("L1", "L2", "L3", "Mi1", "Tm3", "T4a"):
        m = is_r[pre] & (types[post] == t)
        if m.sum():
            print(f"  R -> {t:4s}: {int(m.sum()):4d} edges, "
                  f"{int((w[m] > 0).sum()):4d} excitatory, {int((w[m] < 0).sum()):4d} inhibitory")

    print("\nsides:", Counter(a["side"].tolist()))
    print("az/el available for",
          f"{int(np.isfinite(a['el']).sum()):,} neurons")

    # T4/T5 -> tangential -> descending path sizes
    t4 = np.isin(types, ["T4a", "T4b", "T4c", "T4d", "T5a", "T5b", "T5c", "T5d"])
    tang = np.array(a["g_tangential"], dtype=int)
    dn = np.array(a["g_descending"], dtype=int)
    print(f"\npath: R -> lamina -> medulla -> T4/T5 ({int(t4.sum()):,}) -> "
          f"lobula plate ({len(tang):,}) -> descending ({len(dn):,})")
    for name, src in (("T4/T5 -> tangential", t4), ("T4/T5 -> descending", t4),
                      ("tangential -> descending", np.zeros_like(t4))):
        s = np.zeros(meta["n_neurons"], bool)
        s[src] = True
        tgt = tang if "tangential" in name.split(" -> ")[1] else dn
        m = s[pre] & np.isin(post, tgt)
        print(f"  {name:26s} {int(m.sum()):5d} edges "
              f"({int(np.abs(w[m]).sum()):,} synapses)")


if __name__ == "__main__":
    sys.exit(main())
