"""Build the FlyBrain circuit from the *real* FlyWire 783 connectome.

Data sources (all public):
  * Connectivity_783.parquet / Completeness_783.csv
      The published connectivity of the FlyWire 783 whole-brain connectome,
      as prepared for the leaky integrate-and-fire model of
      Shiu et al., Nature 2024 ("A Drosophila computational brain model
      reveals sensorimotor processing").  One row per connected neuron pair,
      `Connectivity` = number of synapses, `Excitatory` = +1/-1 sign.
  * neuron_annotations.tsv
      flyconnectome/flywire_annotations, Schlegel et al. 2024 /
      Matsliah et al. 2024.  Per-neuron cell type, predicted transmitter,
      side and 3D position.

What this script derives, purely from the data above:
  * a pruned sprite of the fly brain containing the visual system and the
    descending (motor-command) neurons,
  * a retinotopic coordinate (azimuth, elevation) for every neuron, obtained
    by propagating photoreceptor coordinates along the connectome
    (connectome-weighted input centroid = receptive-field centre),
  * a four-channel direction tuning for every neuron, obtained by propagating
    the one-hot preferred directions of the T4/T5 elementary motion detectors
    along the connectome,
  * explicit validation of the sign convention and of the retinotopy.

Output: web/circuit.bin (typed arrays) + web/circuit.json (metadata).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import scipy.sparse as sp

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "web"

PHOTO_TYPES = ("R1-6", "R7", "R8")
MOTION_SUBTYPES = ("T4a", "T4b", "T4c", "T4d", "T5a", "T5b", "T5c", "T5d")
# One-hot preferred-direction channel per elementary motion detector subtype.
# The four subtypes of T4 and of T5 tile the four cardinal directions; the
# assignment below is from Maisak et al. 2013 (Nature 500:212), and which of the
# vertical pair is up versus down is settled by the moving-grating experiment
# run on the model itself (tests/physiology.js).
DIR_CHANNELS = {
    "T4a": 0, "T5a": 0,   # front-to-back
    "T4b": 1, "T5b": 1,   # back-to-front
    "T4c": 2, "T5c": 2,   # vertical, one sign
    "T4d": 3, "T5d": 3,   # vertical, the other sign
}
DIR_NAMES = ["front-to-back", "back-to-front", "up", "down"]

# Unit vector of each subtype's preferred direction expressed in retinal
# coordinates (azimuth grows towards the front, elevation grows dorsally).
# Used to work out on which side of a T4/T5 dendrite a given presynaptic partner
# sits, which is what the elementary motion detector is built out of.
SUBTYPE_AXIS = {
    "T4a": (1.0, 0.0), "T5a": (1.0, 0.0),    # front-to-back  -> +az
    "T4b": (-1.0, 0.0), "T5b": (-1.0, 0.0),  # back-to-front  -> -az
    "T4c": (0.0, 1.0), "T5c": (0.0, 1.0),    # one vertical sign
    "T4d": (0.0, -1.0), "T5d": (0.0, -1.0),  # the other
}

SYN_THRESHOLD = 5  # synapses; standard reliability threshold in FlyWire analyses


# --------------------------------------------------------------------------
# 1. neurons
# --------------------------------------------------------------------------
def load_neurons() -> tuple[pd.DataFrame, np.ndarray]:
    comp = pd.read_csv(RAW / "Completeness_783.csv", index_col=0)
    root_ids = comp.index.to_numpy(dtype="int64")

    ann = pd.read_csv(
        RAW / "neuron_annotations.tsv", sep="\t", low_memory=False,
        usecols=["root_id", "pos_x", "pos_y", "pos_z", "soma_x", "soma_y",
                 "soma_z", "super_class", "cell_class", "cell_type", "top_nt",
                 "top_nt_conf", "side", "flow"],
    )
    ann["root_id"] = ann["root_id"].astype("int64")
    idx_of = pd.Series(np.arange(len(root_ids)), index=root_ids)
    ann["idx"] = ann["root_id"].map(idx_of)
    ann = ann[ann["idx"].notna()].copy()
    ann["idx"] = ann["idx"].astype("int64")

    is_r = ann["cell_type"].isin(PHOTO_TYPES)
    keep = (
        ann["super_class"].eq("optic")
        | ann["super_class"].eq("visual_projection")
        | ann["super_class"].eq("visual_centrifugal")
        | ann["super_class"].eq("descending")
        | is_r
    )
    sel = ann[keep].sort_values("idx").reset_index(drop=True)
    sel["local"] = np.arange(len(sel), dtype="int64")
    print(f"[neurons] {len(sel):,} of {len(ann):,} annotated neurons selected "
          f"(optic lobe + photoreceptors + visual projection + "
          f"centrifugal + descending)")
    print(f"[neurons]   of which photoreceptors R1-6/R7/R8: {int(is_r[keep].sum()):,}")
    return sel, root_ids


# --------------------------------------------------------------------------
# 2. edges
# --------------------------------------------------------------------------
def load_edges(sel: pd.DataFrame, n_total: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    local_of = np.full(n_total, -1, dtype="int64")
    local_of[sel["idx"].to_numpy()] = sel["local"].to_numpy()

    pf = pq.ParquetFile(RAW / "Connectivity_783.parquet")
    pre_p, post_p, w_p = [], [], []
    n_raw = n_pairs = 0
    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(
            rg, columns=["Presynaptic_Index", "Postsynaptic_Index",
                         "Connectivity", "Excitatory"]).to_pandas()
        n_raw += len(t)
        a = t["Presynaptic_Index"].to_numpy()
        b = t["Postsynaptic_Index"].to_numpy()
        s = t["Connectivity"].to_numpy()
        e = t["Excitatory"].to_numpy()
        la, lb = local_of[a], local_of[b]
        m = (la >= 0) & (lb >= 0) & (s >= SYN_THRESHOLD)
        n_pairs += int(m.sum())
        pre_p.append(la[m].astype("uint32"))
        post_p.append(lb[m].astype("uint32"))
        w_p.append((s[m] * e[m]).astype("float32"))
        del t, a, b, s, e, la, lb, m
    pre = np.concatenate(pre_p)
    post = np.concatenate(post_p)
    w = np.concatenate(w_p)
    print(f"[edges]   whole brain: {n_raw:,} pairs; kept {len(pre):,} pairs "
          f"with >= {SYN_THRESHOLD} synapses and both ends in the sprite")
    print(f"[edges]   {int(np.abs(w).sum()):,} synapses, "
          f"{int((w > 0).sum()):,} excitatory / {int((w < 0).sum()):,} inhibitory")
    return pre, post, w


def verify_sign_convention(sel: pd.DataFrame, pre: np.ndarray, w: np.ndarray) -> None:
    """Cross-check the published excitatory sign against predicted transmitter."""
    sign = np.zeros(len(sel), dtype="int8")
    sign[pre[w > 0]] = 1
    sign[pre[w < 0]] = -1
    nt = sel["top_nt"].to_numpy()
    ours = np.where(np.isin(nt, ["acetylcholine", "glutamate"]), 1,
                    np.where(nt == "gaba", -1, 0))
    have = (sign != 0) & (ours != 0)
    agree = (sign[have] == ours[have]).mean()
    print(f"[sign]    transmitter -> sign rule used by the published model "
          f"agrees with 'excitatory unless GABA' for {100 * agree:.2f}% of "
          f"{int(have.sum()):,} neurons")
    tab = pd.crosstab(pd.Series(nt[have], name="predicted_nt"),
                      pd.Series(np.where(sign[have] > 0, "+1", "-1"), name="published_sign"))
    print("[sign]    crosstab of predicted transmitter vs published sign:")
    for line in tab.to_string().splitlines():
        print("[sign]      " + line)
    print("[sign]    note: the published model treats glutamate as inhibitory "
          "(GluCl), which is why the naive ACh/Glu->+ rule disagrees.")


# --------------------------------------------------------------------------
# 3. retinotopy: propagate photoreceptor coordinates along the connectome
# --------------------------------------------------------------------------
def derive_retinotopy(sel: pd.DataFrame, pre, post, w) -> tuple[np.ndarray, np.ndarray, dict]:
    """Recover a visual-field coordinate for every neuron.

    The FAFB image stack is anisotropic (4 x 4 x 40 nm voxels), so positions are
    first converted to nanometres.  In that frame the left/right axis is x, the
    anterior-posterior axis is y and the dorso-ventral axis is z (both verified
    below from the data).  Photoreceptors are therefore laid out on a (y, z)
    sheet: y is the azimuth of the visual field, z is its elevation.
    Every other neuron then inherits the synapse-weighted centroid of its
    presynaptic partners' visual-field coordinates -- i.e. a receptive-field
    centre read straight off the wiring diagram.
    """
    n = len(sel)
    scale = np.array([4.0, 4.0, 40.0])  # FAFB voxel size in nm
    pos = sel[["pos_x", "pos_y", "pos_z"]].to_numpy(dtype="float64") * scale
    sides = sel["side"].to_numpy()
    ct = sel["cell_type"].to_numpy()

    # --- verify the anatomical axes from the data itself ----------------
    sep = [abs(np.nanmean(pos[sides == "left", k]) - np.nanmean(pos[sides == "right", k]))
           for k in range(3)]
    lat_axis = int(np.argmax(sep))
    ocelli = sel["cell_class"].eq("ocellar").to_numpy()
    eye = sel["cell_type"].eq("R1-6").to_numpy()
    dv_sign = "dorsal" if np.nanmedian(pos[ocelli, 2]) > np.nanmedian(pos[eye, 2]) else "ventral"
    print(f"[frame]   left/right axis = -{'xyz'[lat_axis]}- (left/right mean gap "
          f"{max(sep) / 1000:.0f} um)")
    print(f"[frame]   ocelli (dorsal photoreceptors) sit at higher z than the "
          f"retina -> +z points {dv_sign}")
    print(f"[frame]   median position (um) along the visual pathway, per eye:")
    for label in ("R1-6", "L1", "Mi1", "T4a", "Tlp1"):
        line = f"            {label:6s}"
        for side in ("left", "right"):
            m = (ct == label) & (sides == side)
            if m.any():
                v = np.nanmedian(pos[m], axis=0) / 1000
                line += f"  {side[0]}:({v[0]:6.0f},{v[1]:6.0f},{v[2]:5.0f})"
        print(line)

    az = np.full(n, np.nan)
    el = np.full(n, np.nan)
    az[eye] = pos[eye, 1]   # anterior-posterior -> azimuth
    el[eye] = pos[eye, 2]   # dorso-ventral     -> elevation

    # per-eye: centre on the seen field and normalise to [-1, 1]
    frame_report = {}
    for side in ("left", "right"):
        m = eye & (sides == side)
        rep = {}
        for name, arr in (("az", az), ("el", el)):
            v = arr[m]
            lo, hi = np.percentile(v, 0.5), np.percentile(v, 99.5)
            arr[m] = np.clip((v - lo) / (hi - lo), 0, 1) * 2 - 1
            rep[name] = [float(lo / 1000), float(hi / 1000)]
        frame_report[side] = dict(n=int(m.sum()), span_um=rep,
                                  z_is_dorsal=dv_sign == "dorsal")
    print(f"[frame]   retinal field normalised per eye: "
          f"az = y (anterior +), el = z (dorsal +)")

    # --- propagate along the connectome, layer by layer -----------------
    seeded = np.isfinite(az) & np.isfinite(el)
    AZ, EL = az.copy(), el.copy()
    AZ[~seeded] = 0.0
    EL[~seeded] = 0.0
    known = seeded.copy()
    A_all = sp.csr_matrix((np.abs(w), (post, pre)), shape=(n, n))
    for it in range(14):
        keep = known[pre]
        if not keep.any():
            break
        A = sp.csr_matrix((np.abs(w)[keep], (post[keep], pre[keep])), shape=(n, n))
        s = np.asarray(A.sum(axis=1)).ravel()
        newly = (s > 0) & ~known
        if not newly.any():
            break
        AZ[newly] = (A @ AZ)[newly] / s[newly]
        EL[newly] = (A @ EL)[newly] / s[newly]
        known |= newly
        print(f"[rf]      layer {it + 1}: +{int(newly.sum()):,} neurons now have a "
              f"retinal receptive field center ({int(known.sum()):,} total)")

    # A plain input centroid is blurred: wide-field cells that pool over the
    # whole eye pull every receptive field towards the middle of the eye.  Two
    # passes of local refinement keep only the partners that lie near the
    # current estimate, which is the columnar input that actually defines a
    # neuron's receptive field.
    for radius in (0.10, 0.04, 0.015):
        daz = AZ[pre] - AZ[post]
        dele = EL[pre] - EL[post]
        near = np.isfinite(daz) & np.isfinite(dele) & (np.hypot(daz, dele) < radius)
        d = np.abs(w) * near
        A = sp.csr_matrix((d, (post, pre)), shape=(n, n))
        s = np.asarray(A.sum(axis=1)).ravel()
        ok = s > 0
        upd = ok & ~seeded
        AZ[upd] = (A @ AZ)[upd] / s[upd]
        EL[upd] = (A @ EL)[upd] / s[upd]
        print(f"[rf]      local refinement r={radius}: "
              f"{int(upd.sum()):,} neurons sharpened")
    AZ[~known] = np.nan
    EL[~known] = np.nan
    covered = int(known.sum())
    # how elevation-resolved are the descending neurons really? (sanity)
    dnm = sel["super_class"].eq("descending").to_numpy() & np.isfinite(EL)
    if dnm.any():
        print(f"[rf]      descending neurons: {int(dnm.sum()):,} with a retinal "
              f"field, elevation centroid spread (sd) = "
              f"{np.nanstd(EL[dnm]):.3f} of the field")
    print(f"[rf]      retinotopic coordinate assigned to {covered:,}/{n:,} "
          f"neurons ({100 * covered / n:.1f}%)")
    return AZ, EL, frame_report


# --------------------------------------------------------------------------
# 4. direction tuning propagated from T4/T5
# --------------------------------------------------------------------------
def _propagate_downstream(seed_vals: np.ndarray, seed: np.ndarray,
                          n: int, pre, post, w, passes: int = 12) -> np.ndarray:
    """Spread a per-neuron vector along the connectome, following synapses in
    their physiological direction, one synaptic layer per pass."""
    V = seed_vals.astype("float64").copy()
    known = seed.copy()
    for it in range(passes):
        keep = known[pre]
        if not keep.any():
            break
        d = np.abs(w)[keep].astype("float64")
        A = sp.csr_matrix((d, (post[keep], pre[keep])), shape=(n, n))
        s = np.asarray(A.sum(axis=1)).ravel()
        newly = (s > 0) & ~known
        if not newly.any():
            break
        V[newly] = (A @ V)[newly] / s[newly]
        known |= newly
    V[~known] = 0.0
    return V


def derive_direction(sel: pd.DataFrame, pre, post, w) -> np.ndarray:
    n = len(sel)
    ct = sel["cell_type"].to_numpy()
    seed = np.isin(ct, MOTION_SUBTYPES)
    print(f"[dir]     {int(seed.sum()):,} T4/T5 elementary motion detectors seeded "
          f"({', '.join(DIR_NAMES)}) at their axon terminals in the lobula plate")
    COLS = []
    for st in MOTION_SUBTYPES:
        one = np.zeros(n, dtype="float64")
        one[ct == st] = 1.0
        COLS.append(_propagate_downstream(
            np.where(seed, one, 0.0) * seed, seed, n, pre, post, w))
    T = np.stack(COLS, axis=1)          # (n, 8) one column per subtype
    print(f"[dir]     four-channel direction drive reaches "
          f"{int((T.sum(axis=1) > 0).sum()):,} of {n:,} neurons")
    return T.astype("float32")


def compute_motion_geometry(sel: pd.DataFrame, pre, post, w,
                            AZ: np.ndarray, EL: np.ndarray) -> np.ndarray:
    """Work out, for every synapse onto an elementary motion detector, on which
    side of that detector's dendrite the partner sits.

    A T4/T5 cell is a delay-and-correlate detector: it compares the signal from
    its own column with a signal from a neighbouring column, and only fires when
    the two coincide, which happens for motion in one direction.  Which side is
    which follows directly from the wiring: a presynaptic partner whose
    receptive field lies on one side of the cell's own receptive field supplies
    the delayed arm (Shinomiya et al. 2019; Haag et al. 2016, 2017).

    Returns a float array, one value per synapse, holding the projection of the
    partner's retinal offset onto the detector's preferred axis, in units of the
    visual field.  Positive means the partner sits on the preferred side.
    """
    ct = sel["cell_type"].to_numpy()
    axis = np.zeros((len(sel), 2))
    for st, (ux, uy) in SUBTYPE_AXIS.items():
        m = ct == st
        axis[m, 0] = ux
        axis[m, 1] = uy
    daz = np.nan_to_num(AZ[pre] - AZ[post])
    dele = np.nan_to_num(EL[pre] - EL[post])
    proj = daz * axis[post, 0] + dele * axis[post, 1]
    # only columnar partners define an arm of the dendrite; wide-field feedback
    # cells are far away in the field and must not be treated as one of the arms
    dist = np.hypot(AZ[pre] - AZ[post], EL[pre] - EL[post])
    proj = np.where(np.isfinite(dist) & (dist < 0.05), proj, 0.0)
    is_motion = np.isin(ct[post], MOTION_SUBTYPES)
    proj = np.where(is_motion, proj, 0.0).astype("float32")
    n_arm = int(np.count_nonzero(proj))
    pos = int((proj > 0).sum())
    print(f"[motion]  {n_arm:,} of {int(is_motion.sum()):,} motion-detector synapses "
          f"sit on a columnar arm of the dendrite ({pos:,} on the preferred side, "
          f"{n_arm - pos:,} on the null side)")
    return proj


def check_readout_reach(sel: pd.DataFrame, T: np.ndarray, AZ: np.ndarray,
                        EL: np.ndarray) -> dict:
    """Does the visual motion signal actually reach the motor-command neurons?

    T4/T5 are elementary motion detectors; they never touch descending neurons
    directly.  The point of this check is that the direction drive we propagated
    along the wiring diagram arrives at the lobula plate tangential cells and at
    the descending neurons -- i.e. that a real sensorimotor path exists.
    """
    ct = sel["cell_type"].to_numpy()
    sc = sel["super_class"].to_numpy()
    has = T.sum(axis=1) > 0
    rep = {}
    print("[reach]   neurons receiving a direction-selective drive from T4/T5:")
    for label, m in (("optic lobe", sc == "optic"),
                     ("lobula plate tangential",
                      pd.Series(ct).str.match(r"^(Tlp|VS|HS|LPi|LPT)", na=False).to_numpy()),
                     ("visual projection neurons", sc == "visual_projection"),
                     ("descending neurons", sc == "descending")):
        n_hit = int((m & has).sum())
        rep[label] = n_hit
        print(f"            {label:26s} {n_hit:>7,} / {int(m.sum()):,}")

    dn = (sc == "descending") & np.isfinite(EL)
    if dn.any():
        E = EL[dn]
        print(f"[reach]   descending-neuron receptive-field elevation spans "
              f"{E.min():+.2f}..{E.max():+.2f} "
              f"(sd {E.std():.3f}); azimuth spans {AZ[dn].min():+.2f}.."
              f"{AZ[dn].max():+.2f}")
        # how much of each channel reaches the upper vs lower half of the field
        up = E > 0
        for i, ch in enumerate(DIR_NAMES):
            cols = [j for j, st in enumerate(MOTION_SUBTYPES) if DIR_CHANNELS[st] == i]
            v = T[dn][:, cols].sum(axis=1)
            print(f"            {ch:14s} drive to lower-field DNs "
                  f"{v[~up].sum():7.1f}   upper-field DNs {v[up].sum():7.1f}")
    return rep


# --------------------------------------------------------------------------
# 5. write
# --------------------------------------------------------------------------
DTYPES = {np.dtype("float32"): 0, np.dtype("float64"): 1, np.dtype("int32"): 2,
          np.dtype("uint32"): 3, np.dtype("int8"): 4, np.dtype("uint8"): 5,
          np.dtype("int64"): 6, np.dtype("uint16"): 7}
DTYPE_NAMES = {v: k.name for k, v in DTYPES.items()}


def write_circuit(path_bin: Path, path_json: Path, arrays: dict, meta: dict) -> None:
    """Tiny typed-array container: magic, JSON header, then named blocks.

    Block layout: u32 name length, name, u8 dtype code, u8 ndim, u32 dims[],
    u32 byte length, raw little-endian data.
    """
    import struct
    meta["blocks"] = {n: {"dtype": DTYPE_NAMES[DTYPES[np.ascontiguousarray(a).dtype]],
                          "shape": list(np.ascontiguousarray(a).shape)}
                      for n, a in arrays.items()}
    with open(path_bin, "wb") as fh:
        fh.write(b"FLYBRN01")
        js = json.dumps(meta).encode()
        fh.write(struct.pack("<I", len(js)))
        fh.write(js)
        fh.write(struct.pack("<I", len(arrays)))
        for name, arr in arrays.items():
            arr = np.ascontiguousarray(arr)
            nb = name.encode()
            fh.write(struct.pack("<I", len(nb)))
            fh.write(nb)
            fh.write(struct.pack("<BB", DTYPES[arr.dtype], arr.ndim))
            for d in arr.shape:
                fh.write(struct.pack("<I", int(d)))
            raw = arr.tobytes()
            fh.write(struct.pack("<I", len(raw)))
            fh.write(raw)
    print(f"[write]   {path_bin} ({path_bin.stat().st_size / 1e6:.1f} MB)")
    path_json.write_text(json.dumps(meta, indent=1))


def main() -> None:
    sel, root_ids = load_neurons()
    pre, post, w = load_edges(sel, len(root_ids))
    verify_sign_convention(sel, pre, w)
    AZ, EL, eye_frame = derive_retinotopy(sel, pre, post, w)
    T = derive_direction(sel, pre, post, w)
    mproj = compute_motion_geometry(sel, pre, post, w, AZ, EL)
    geom = {"reach": check_readout_reach(sel, T, AZ, EL)}

    # group indices
    ct = sel["cell_type"].to_numpy()
    sc = sel["super_class"].to_numpy()
    groups = {}
    for st in MOTION_SUBTYPES:
        groups[st] = np.flatnonzero(ct == st)
    groups["photoreceptors"] = np.flatnonzero(sel["cell_type"].isin(PHOTO_TYPES).to_numpy())
    groups["descending"] = np.flatnonzero(sc == "descending")
    groups["tangential"] = np.flatnonzero(pd.Series(ct).str.match(r"^(Tlp|VS|HS|LPi|LPT)", na=False).to_numpy())
    groups["visual_projection"] = np.flatnonzero(sc == "visual_projection")
    for k, v in groups.items():
        print(f"[groups]  {k:20s} {len(v):,}")

    arrays = {
        "pre": pre.astype("uint32"),
        "post": post.astype("uint32"),
        "w": w.astype("float32"),
        "pos": sel[["pos_x", "pos_y", "pos_z"]].to_numpy(dtype="float32"),
        "az": AZ.astype("float32"),
        "el": EL.astype("float32"),
        "dir": T,
        "mproj": mproj,
        "root_id": sel["root_id"].to_numpy(dtype="int64"),
        "side": np.where(sel["side"].eq("left"), 1,
                         np.where(sel["side"].eq("right"), 2, 0)).astype("int8"),
    }
    # four-channel direction drive (a/b/c/d collapsed per Maisak et al. 2013)
    dir4 = np.zeros((len(sel), 4), dtype="float64")
    for st, ch in DIR_CHANNELS.items():
        dir4[:, ch] += T[:, list(MOTION_SUBTYPES).index(st)]
    nz = dir4.sum(axis=1, keepdims=True)
    nz[nz == 0] = 1
    arrays["dir4"] = (dir4 / nz).astype("float32")

    sc_names = sorted(set(sel["super_class"].fillna("unknown")))
    sc_map = {s: i for i, s in enumerate(sc_names)}
    arrays["scode"] = sel["super_class"].fillna("unknown").map(sc_map).to_numpy(dtype="uint8")
    ct_names = sorted(set(sel["cell_type"].fillna("unknown")))
    ct_map = {s: i for i, s in enumerate(ct_names)}
    arrays["ctid"] = sel["cell_type"].fillna("unknown").map(ct_map).to_numpy(dtype="uint32")
    for k, v in groups.items():
        arrays[f"g_{k}"] = v.astype("uint32")

    meta = {
        "source": {
            "connectome": "FlyWire 783 (Dorkenwald et al. 2024, Nature)",
            "connectivity_table": "shiu/Drosophila_brain_model Connectivity_783.parquet",
            "annotations": "flyconnectome/flywire_annotations Supplemental_file1",
            "doi": "10.5281/zenodo.10676866",
            "synapse_threshold": SYN_THRESHOLD,
        },
        "n_neurons": int(len(sel)),
        "n_edges": int(len(pre)),
        "n_synapses": int(np.abs(w).sum()),
        "dir_names": list(MOTION_SUBTYPES),
        "dir_channel_names": DIR_NAMES,
        "eye_frame": eye_frame,
        "validation": geom,
        "cell_types": ct_names,
        "super_classes": sc_names,
        "nt": sel["top_nt"].fillna("unknown").tolist(),
    }
    write_circuit(OUT / "circuit.bin", OUT / "circuit.json", arrays, meta)
    print("[done]    circuit written to", OUT)


if __name__ == "__main__":
    sys.exit(main())
