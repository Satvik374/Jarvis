# FlyBrain

A Flappy Bird game played by a spiking simulation of the **real fruit fly brain**.

Not a bot with hand-written rules. The bird flapping its wings is the output of
97,973 neurons and 1.5 million synapses taken from the FlyWire 783 connectome of
an adult female *Drosophila melanogaster*, simulated as leaky integrate-and-fire
cells at 1 ms resolution. The game feeds the fly's 10,582 photoreceptors; its
descending neurons — the real cells that carry motor commands out of the fly's
brain — decide when to beat the wings.

---

## 1. The research: what fly brain data is actually open

The adult fruit fly brain was fully mapped in 2024. The result is public, and
this project uses it directly rather than a stand-in.

| What | Where | Notes |
|---|---|---|
| **FlyWire 783 whole-brain connectome** | `doi:10.5281/zenodo.10676866` | 139,255 proofread neurons, ~50M synapses, ~130M synapse rows. Dorkenwald et al., *Nature* 2024 |
| **Signed, model-ready connectivity** | `github.com/philshiu/Drosophila_brain_model` → `Connectivity_783.parquet` | 15,091,983 neuron-to-neuron edges with `Connectivity` (synapse count) and `Excitatory` (+1/−1). Prepared for Shiu et al., *Nature* 2024 |
| **Per-neuron annotations** | `github.com/flyconnectome/flywire_annotations` → `Supplemental_file1_neuron_annotations.tsv` | cell type, predicted transmitter, brain side, 3D position. Schlegel et al. 2024 / Matsliah et al. 2024 |

The one that makes everything possible is the third column: the annotation table
carries `pos_x/pos_y/pos_z` and `soma_*` for every neuron, so the dataset
includes **where each cell sits**, not just what it connects to. Without that
there would be no way to give the simulated fly a retina.

### Why this is the right connectome to use

FlyWire 783 is the release behind the whole-brain model of Shiu et al., *Nature*
2024 ("A Drosophila computational brain model reveals sensorimotor processing").
The connectivity table used here is the exact artefact that model was built from,
which means:

* the sign convention (excitatory vs inhibitory) is a **published, peer-reviewed**
  choice rather than something invented here, and
* the LIF equations below are those of a model that has already been validated
  against real fly behaviour.

### What the fly's visual system looks like in this data

The sub-circuit that matters for Flappy Bird:

```
R1-6 / R7 / R8  photoreceptors      (10,582)
      |  histamine, sign-inverting
   Lamina      L1 L2 L3 L4 L5, T1, Am, C2, C3, Lawf1/2      ON and OFF channels
      |
   Medulla     Mi1 Mi4 Mi9, Tm1 Tm2 Tm3 Tm4 Tm9, T2 T3 ...
      |        the first stage at which motion direction exists in the real fly
   Lobula plate   T4a-d and T5a-d: 12,246 elementary motion detectors,
      |            four subtypes tiling four cardinal directions
   Tangential cells  Tlp1-14, VS1-8, HS, LPi...   (1,185)   wide-field optic flow
      |
   Lobula columnar  LC4, LPLC1/2/4, LLPC1/2, LT51 ...  (visual projection neurons)
      |
   Descending neurons  (1,299)  -- the fly's motor command leaves the brain here
```

The anatomy came out of the data on its own. `build/make_circuit.py` prints the
median position of each stage and the left/right axis separation, which is how
the retinal coordinate frame below was pinned down.

---

## 2. How the circuit was built

### Selection

Neurons kept: `super_class` in {optic (77,530), sensory photoreceptors
(10,582), visual_projection (8,038), visual_centrifugal (524), descending
(1,299)}. Total **97,973** of the 138,625 annotated cells.

Edges kept: both endpoints inside that set **and** at least 5 synapses.
Thresholding weak connections is standard in FlyWire analyses because 1–2
synapse contacts are not reliably detected. That leaves **1,499,348 edges**
carrying **16,092,786 synapses** (907,954 excitatory / 591,394 inhibitory).

### Sign convention, checked not assumed

The published table gives each presynaptic neuron a single sign. Cross-checking
against the predicted transmitter shows the rule is *not* the naive
"ACh/Glu positive":

```
                       published +1   published -1
  acetylcholine             59,854          1,644
  gaba                         262         12,807
  glutamate                    704         14,715
```

Glutamate is treated as **inhibitory** (GluCl), which is why the naive rule only
agrees for 81.5% of cells. The published convention is used unchanged.

### Reconstructing the retina's coordinate frame from anatomy

FAFB voxels are 4 × 4 × 40 nm, so positions are converted to nanometres first.
The script then *verifies* the axes rather than assuming them:

* the left/right axis is whichever coordinate separates the two eyes most —
  found to be **x** (490 µm mean gap);
* the ocelli (the fly's dorsal simple eyes) sit at higher **z** than the retina,
  so **+z is dorsal**;
* walking down the visual pathway, x goes 174 → 225 → 256 → 311 µm, i.e. the
  retina, lamina, medulla and lobula plate lie in a clean lateral-to-medial
  stack, as they should.

Photoreceptors therefore sit on a **(y, z) sheet**: y is azimuth (anterior
positive), z is elevation (dorsal positive).

### Receptive fields, by propagation along the connectome

Every neuron then inherits the synapse-weighted centroid of its presynaptic
partners' retinal coordinates, computed layer by layer. A plain centroid is
badly blurred because wide-field cells pull every receptive field toward the
middle of the eye, so it is followed by three passes of local refinement at
radii 0.10, 0.04 and 0.015, keeping only partners near the current estimate.
Result: **94,995 / 97,973 neurons (97%)** get a retinal receptive-field centre,
and the descending neurons end up with elevation centroids spanning −0.79…+0.73
of the field (sd 0.33) — genuinely elevation-resolved, which is what the motor
decoder needs.

---

## 3. The simulation

Equations, constants and the per-synapse weight all follow Shiu et al. 2024:

```
tau_m dV/dt = (V_rest - V + g)        V_rest = -52 mV, V_th = -45 mV
tau     dg/dt = -g                    refractory 2.2 ms, tau_m = 20 ms
on a presynaptic spike:  g += w       synaptic delay 1.8 ms
```

Four things were added on top, all documented and all biophysical:

1. **Homeostatic synaptic scaling.** The connectome fixes how strong each
   connection is *relative* to the other inputs of the same postsynaptic cell.
   The absolute scale is a free constant (a real neuron sets its own total
   synaptic strength), and the published per-synapse weight is calibrated for a
   network driven by very strong external Poisson input. Each neuron is
   therefore given the same total input strength `wTotal` (200 mV), distributed
   across its synapses in proportion to the real synapse counts.
2. **An excitation/inhibition ratio** (`inhibScale`, 0.25). Synapse counts do
   not translate into equal postsynaptic charge for the two, because they use
   different receptors.
3. **Spontaneous background input** — a Poisson train of small conductance kicks.
   A noiseless network of identical cells collapses into a synchronous state and
   can carry no information.
4. **Per-cell-type synaptic time constants**, from the measured temporal kernels
   of the cells that feed the motion detectors, plus column-dependent conduction
   delays (see §4).

Nothing in `sim/flybrain.js` knows what Flappy Bird is.

---

## 4. The honest finding: this model does not see motion direction

The whole point of T4/T5 is direction selectivity, so `tests/physiology.js`
reproduces the experiment of Maisak et al. 2013 on the model: present a
full-field moving grating and record which of the eight subtypes responds.

**It doesn't work.** Discrimination indices come out at 0.1–0.5% — the eight
subtypes respond identically to all four directions. The wiring is there
(51,498 synapses from T4/T5 onto the lobula plate, and the direction drive
propagates to 1,170 tangential cells and 1,061 descending neurons) but the
computation is not.

This is not a bug that was left in, it is a real result, and the reason is
understood: **direction selectivity comes from the dendrite, not the wiring
diagram.** T4/T5 compute it with a three-arm arrangement — a delayed amplifier
on the preferred side, a fast central exciter, and a delayed inhibitor on the
null side (Haag et al. 2016, 2017; Shinomiya et al. 2019). A point-neuron LIF
with one synaptic time constant per cell type has no way to express that
comparison.

Two separate attempts were made to recover it from the data:

* deriving each detector's preferred direction from the offset between its own
  receptive field and that of its presynaptic partners. The offsets came out
  symmetric around zero for every subtype (±0.025 of the field, means ≈ 0) —
  the centroid estimator cannot resolve the arms.
* assigning per-synapse delays proportional to that offset. Only 26,863 of
  96,961 motion-detector synapses had a resolvable columnar arm, and the
  preferred/null split was 13,422 / 13,441, i.e. chance.

The mechanism is therefore switched off by default (`motionDelayGain: 0`) rather
than left in as unearned decoration. Reproducing direction selectivity needs a
multi-compartment T4/T5 model — that is the natural next project, not something
to fake.

---

## 5. How the fly plays anyway

Flappy Bird's decisive cue is not motion, it is **where the gap is**. And the
gap is a bright hole punched through dark pipes, so it is available from the
spatial pattern of activity in the visual system — no direction selectivity
needed.

**Retina.** Each photoreceptor samples the world at its own receptive-field
direction, through a window 220 × 620 world pixels centred on the eye. Because
the eye rides on the bird, the scene shifts vertically with the bird's own
motion: this is real optic flow, not a scripted readout.

**Decoder.** A population decoder over the **1,061 real descending neurons that
have a retinal field**:

```
wPos[i] ∝ (T4/T5 direction drive into DN i) × (elevation of DN i's receptive field)
```

Both factors come from the connectome. Bright sky only reaches the eye through
the gap, so the elevation-weighted visual drive of the descending population
estimates where the gap sits relative to the heading.

**Validated as a sensor**, not asserted. `tests/tuning.js` holds the bird still,
parks a pipe in front of it and slides the gap up and down:

```
  gap elevation (px above bird)      pos readout
                      -160              -5.61
                      -120              -4.90
                       -80              -4.77
                       -40              -2.09
                         0              -1.60
                       +40              +0.58
                       +80              +3.76
```

Monotonic across the useful range, zero crossing near the heading, positive when
the gap is above. In the closed loop the correlation between the readout and the
true gap error is **−0.42** with the correct sign.

**Motor.** A wingbeat is an impulse, so the rate needed to hold altitude is
`gravity / (2 × impulse)` — a property of the body, computed from the game's own
physics, not tuned. The descending readout scales that rate:

```
lambda = lambda_hover * exp(rateGain * command)
```

The command is normalised against its own running spread (fast) and a
**reference direction calibrated during a 500 ms warm-up on a neutral scene**
(frozen). The reference is the only thing on the sensory side that is calibrated
rather than derived, and it is what makes "nothing in front of me" mean "beat the
wings at exactly the rate that holds altitude".

---

## 6. Results

`node tests/play.js --games 12`

```
12 games: scores 0, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0
mean 0.33   best 4   median 0
57.4 s of biology simulated in 111.9 s wall (0.51x real time)
  mean rate  photoreceptors   30.56 Hz
  mean rate  descending        1.65 Hz
  mean rate  lobula plate      8.80 Hz
  mean rate  visual projection 8.32 Hz
```

**The fly plays, and it plays badly.** It holds altitude, reaches the first pipe
in almost every game, and threads it in a minority of runs (best 4 pipes). It is
a genuine sensorimotor loop — a real fly's visual system processing the game's
image, a real fly's descending neurons issuing the command — but the control
signal is weak and the loop is marginal.

Games die in two ways: the fly drifts into a pipe edge before the readout has
enough signal to correct, or it fails to be far enough from the gap when the
pipe arrives. The limiting factor is the signal-to-noise of the descending
population's response, not the game's difficulty. Making the world slower,
making the gaps bigger, and low-pass filtering the motor command were all tried
and all failed to help; and the parameter landscape is chaotic — a few percent
change in `backgroundHz` or `lightGain` flips the mean score between 0 and 0.8,
which is itself a symptom of a control loop operating near the edge of its
signal-to-noise.

The honest summary: **a real connectome of the fly's visual system, fed the real
image from the game, produces a motor command that is reliably *about* the right
thing (it correlates with the gap error at r = −0.42, with the correct sign) but
is not reliable enough to fly well.** Getting from there to competent flight
needs the direction-selective motion pathway of §4 — the same missing piece.

### Proof that it is the brain doing it

If the fly were a hardcoded bot, damaging the biology would change nothing. Each
ablation below keeps the *code path* identical and perturbs only the data:

`node tests/ablation.js --games 12`

| Variant | mean pipes | % of control |
|---|---|---|
| **intact fly** | **0.33** | **100%** |
| shuffled wiring (out-degree preserved, random targets) | 0.00 | 0% |
| shuffled synapse weights | 0.08 | 25% |
| scrambled photoreceptor visual fields | 0.00 | 0% |
| scrambled descending-neuron receptive fields | 0.00 | 0% |
| all T4/T5 direction drive removed | 0.00 | 0% |

The specific wiring, the specific synapse counts, the retinotopy of the eye, the
receptive fields of the motor neurons and the input from the motion detectors
are each necessary. Nothing survives replacing them.

---

## 7. What is real, what is a modelling choice

Being precise about this matters more than the score.

**From the connectome, unmodified:** which neurons exist and how many; every
synapse and its count; which cells are excitatory or inhibitory; each neuron's
cell type, transmitter and 3D position; the retinal coordinate of every neuron's
receptive field; the elevation centroid and direction-channel input of every
descending neuron; which cells are photoreceptors, T4/T5, lobular, tangential,
descending.

**Modelling choices, all free parameters of a biophysical model:** the total
synaptic strength per neuron; the excitatory/inhibitory ratio; the background
noise rate; which cell types get which time constant (values taken from Arenz et
al. 2017, assignment from cell type); the retinal gain; the size of the eye's
field of view; the motor gain; and the reference direction calibrated during
warm-up.

**Not present anywhere:** any rule of the form "if the bird is below the gap,
flap". No game state is read by the decoder. No weights were fit to game
outcomes, and no policy was trained. `sim/flybrain.js` and `sim/fly.js` never
import the game; the game imports them.

**The one structural simplification:** both eyes are shown the same frontal
field. The fly's eyes overlap frontally over most of the game's view, so
inter-ocular disparity was not modelled.

---

## 8. Running it

```bash
cd FlyBrain
python build/make_circuit.py          # rebuild the circuit from the raw data (~3 min)
node tests/physiology.js              # moving-grating experiment on the optic lobe
node tests/tuning.js                  # validate the readout against gap position
node tests/play.js --games 12         # headless closed-loop games
node tests/ablation.js --games 12     # the ablation table above
python -m http.server 8731            # then open web/index.html
```

The browser page needs a server (ES modules + a 33 MB `fetch`). It shows the
game rendered from the same luminance field the photoreceptors are fed, the
fly's brain as a frontal projection of all 97,973 real neuron positions lighting
up as they spike (photoreceptors yellow, descending neurons red), and a live
readout of the motor command.

### Files

```
build/make_circuit.py     data -> circuit: selection, retinotopy, direction drive, export
build/inspect_circuit.py  reference reader for the container
build/explore_scope.py    how big each candidate sub-circuit is
sim/flybrain.js           the LIF connectome simulation (Node and browser)
sim/world.js              the game world as an analytic luminance field
sim/fly.js                retina, descending-neuron decoder, wingbeat generator
sim/game.js               the closed loop
web/index.html            the demo
tests/physiology.js       does the simulated optic lobe see motion direction?
tests/tuning.js           does the readout track the gap?
tests/play.js             headless games
tests/ablation.js         is it the connectome or the code?
tests/operating_point.js  finding the network's physiological regime
data/raw/                 the downloaded public data (see §1)
web/circuit.bin           the extracted circuit, 33 MB
```

`data/raw/` holds ~950 MB of public data and is git-ignored; `build/make_circuit.py`
needs `Connectivity_783.parquet`, `Completeness_783.csv` and
`neuron_annotations.tsv`. The 813 MB `proofread_connections_783.feather` (the
full per-synapse table from Zenodo, `doi:10.5281/zenodo.10676866`) is also there
as the primary source of record and for spot-checking the derived table, but the
build does not read it.

## 9. References

* Dorkenwald, S. et al. **Neuronal wiring diagram of an adult brain.** *Nature* 634, 124–138 (2024).
* Schlegel, P. et al. **Whole-brain annotation and multi-connectome cell typing of Drosophila.** *Nature* 634, 139–152 (2024).
* Matsliah, A. et al. **Neuronal parts list and wiring diagram for a visual system.** *Nature* 634, 166–180 (2024).
* Shiu, P.K. et al. **A Drosophila computational brain model reveals sensorimotor processing.** *Nature* 634, 210–219 (2024).
* Maisak, M.S. et al. **A directional tuning map of Drosophila elementary motion detectors.** *Nature* 500, 212–216 (2013).
* Arenz, A. et al. **The temporal tuning of the Drosophila motion detectors is determined by the dynamics of their input elements.** *Curr Biol* 27, 929–944 (2017).
* Haag, J., Arenz, A., Serbe, E., Gabbiani, F. & Borst, A. **Complementary mechanisms create direction selectivity in the fly.** *eLife* 5, e17421 (2016).
* Shinomiya, K. et al. **Comparisons between the ON- and OFF-edge motion pathways in the Drosophila brain.** *eLife* 8, e40025 (2019).
* Takemura, S. et al. **A visual motion detection circuit suggested by Drosophila connectomics.** *Nature* 500, 175–181 (2013).

## Licence

Code here is yours to do what you like with. The connectome data is released
under its own terms (see the Zenodo record and the Shiu et al. repository) and
the annotations under CC-BY — cite the papers above if you use them.
