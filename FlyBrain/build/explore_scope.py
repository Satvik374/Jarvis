"""Explore how large candidate sub-circuits of the FlyWire 783 connectome are.

Reads the published Shiu et al. (Nature 2024) connectivity table row-group by
row-group so that it never needs the whole 15M-edge table in RAM.

Usage:  python FlyBrain/build/explore_scope.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
CON = RAW / "Connectivity_783.parquet"
COMP = RAW / "Completeness_783.csv"
ANN = RAW / "neuron_annotations.tsv"


def load_annotations() -> pd.DataFrame:
    ann = pd.read_csv(
        ANN,
        sep="\t",
        usecols=[
            "root_id", "pos_x", "pos_y", "pos_z", "soma_x", "soma_y", "soma_z",
            "super_class", "cell_class", "supertype", "cell_type",
            "top_nt", "top_nt_conf", "side", "nerve",
        ],
        low_memory=False,
    )
    ann["root_id"] = ann["root_id"].astype("int64")
    return ann


def main() -> None:
    comp = pd.read_csv(COMP, index_col=0)
    root_ids = comp.index.to_numpy(dtype="int64")
    print(f"proofread/completeness neurons: {len(root_ids):,}")

    ann = load_annotations()
    print(f"annotation rows: {len(ann):,}")

    # Map annotation rows onto the parquet 'Index' ordering used by the model.
    idx_of = {rid: i for i, rid in enumerate(root_ids)}
    ann["idx"] = ann["root_id"].map(idx_of)
    matched = ann["idx"].notna()
    print(f"annotation rows matched to an index: {matched.sum():,} "
          f"({100 * matched.mean():.1f}%)")
    ann = ann[matched].copy()
    ann["idx"] = ann["idx"].astype("int64")

    n = len(root_ids)
    masks: dict[str, np.ndarray] = {}

    def sel(name: str, cond: pd.Series) -> None:
        m = np.zeros(n, dtype=bool)
        m[ann.loc[cond, "idx"].to_numpy()] = True
        masks[name] = m
        print(f"  {name:28s} {m.sum():>8,} neurons")

    print("\nneuron classes:")
    sel("photoreceptor", ann["cell_class"].eq("photoreceptor"))
    sel("optic(super_class)", ann["super_class"].eq("optic"))
    sel("sensory(other)", ann["super_class"].eq("sensory")
        & ~ann["cell_class"].eq("photoreceptor"))
    sel("visual_projection", ann["super_class"].eq("visual_projection"))
    sel("visual_centrifugal", ann["super_class"].eq("visual_centrifugal"))
    sel("descending", ann["super_class"].eq("descending"))
    sel("central", ann["super_class"].eq("central"))
    sel("motor", ann["super_class"].eq("motor"))

    visual = (masks["photoreceptor"] | masks["optic(super_class)"]
              | masks["visual_projection"] | masks["visual_centrifugal"]
              | masks["descending"])
    masks["VISUAL+DN"] = visual
    print(f"  {'UNION visual+DN':28s} {visual.sum():>8,} neurons")

    # T4 / T5 detection by cell_type
    ct = ann["cell_type"].astype(str)
    for pat in ("T4", "T5", "T2", "T3", "HS", "VS", "H1", "H2", "LPT", "Mi1", "Tm3"):
        m = ct.str.fullmatch(pat) | ct.str.startswith(pat + "_") | ct.str.contains(rf"^{pat}[a-d]?$", regex=True)
        print(f"    cell_type matching {pat:5s}: {int(m.sum()):>6,}")

    # ---- edge counting over row groups -------------------------------
    schemes = {
        "VISUAL+DN  (all edges)": visual,
        "VISUAL+DN  (syn>=5)": visual,
        "optic only (all)": masks["optic(super_class)"],
    }
    counts = {k: [0, 0] for k in schemes}  # [n_edges, n_synapses]

    pf = pq.ParquetFile(CON)
    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(
            rg, columns=["Presynaptic_Index", "Postsynaptic_Index", "Connectivity"]
        )
        pre = t.column("Presynaptic_Index").to_numpy()
        post = t.column("Postsynaptic_Index").to_numpy()
        cn = t.column("Connectivity").to_numpy()
        for name, m in schemes.items():
            keep = m[pre] & m[post]
            if name.endswith("(syn>=5)"):
                keep &= cn >= 5
            counts[name][0] += int(keep.sum())
            counts[name][1] += int(cn[keep].sum())
        del t, pre, post, cn

    print("\nedge counts (rows = neuron pairs, not synapses):")
    for name, (e, s) in counts.items():
        print(f"  {name:28s} {e:>12,} edges   {s:>14,} synapses")

    # nt distribution among the VISUAL+DN neurons
    sub = ann[ann["idx"].isin(np.flatnonzero(visual))]
    print("\ntop_nt among VISUAL+DN neurons:")
    print(sub["top_nt"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    sys.exit(main())
