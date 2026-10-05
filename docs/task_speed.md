# Task-loop speed: focused overhead reduction

## What changed

- `jarvis/perception/elements.py`: decide whether a control can be retained
  before reading its cross-process UI Automation `Name` property. Always retain
  document-title checks and traversal through surplus window chrome, so page
  controls remain reachable. Stop expanding children once the element budget is
  full. Measure the walk deadline with a monotonic clock.
- `jarvis/tools/registry.py`: an element-matching `wait_for` returns the fresh
  observation it just collected. Window-title matches and timeouts do not return
  a potentially stale observation.
- `jarvis/agent/loop.py`: consume that fresh observation rather than immediately
  repeating the UIA/OCR pass. When a tool-provided image is already selected for
  the turn, do not capture an unrelated desktop image only to discard it. The
  completion verifier sees the selected image too. Clearing the tool image
  resumes fresh desktop capture; there is no cross-turn desktop screenshot cache.
- `jarvis/agent/prompts.py`: prefer existing multi-key/multi-file and direct tools,
  concise thoughts, and proceeding immediately when the current result already
  confirms readiness. Screen-dependent steps must still inspect their results.

The provider, model, vision settings, action schema, confirmations, fail-safe,
self-healing and completion verification remain enabled/unchanged. This does not
turn on `-yolo` or introduce arbitrary multi-action execution.

Restart the running Jarvis process to load the changed Python modules. No config
migration or dependency installation is required.

## Measured evidence and limits

Baseline: working tree based on `c418ddf`, including the pre-existing uncommitted
work, **not** pristine HEAD. Measurements used Windows CPython 3.13.4 from WSL.
There were no provider calls in these benchmarks.

| Fixed workload | Before | After |
|---|---:|---:|
| Synthetic UIA tree: 180 offscreen controls, 100 chrome controls, nested document | 283 Name reads | 21 Name reads |
| Same tree: returned elements / child reads | 21 / 104 | 21 / 104 |
| Three-turn tool-image scenario: browser snapshot, non-UI action, finish | 3 desktop captures | 1 desktop capture |
| Successful element wait | immediate duplicate screen read | reuse the handler's fresh read |

The UIA test checks the exact labels, roles, IDs and final target coordinates.
Document naming, unnamed edit fields, traversal through rejected chrome, and
normal post-action refreshes are covered. Tool-image tests cover verification
and restoring fresh local capture after the external image is cleared.

### Timing is not an end-to-end speed claim

The synthetic traversal benchmark uses in-process fake controls, so it measures
Python overhead, not the cost of Windows cross-process calls. Thirty warm
samples after one warmup gave:

- Before: median **2.771 ms**, range **2.721–3.482 ms**.
- After: median **2.834 ms**, range **2.748–3.109 ms**.

Those timings overlap and show **no demonstrated wall-clock improvement** in the
fake-control workload. The reliable result is removal of 262 unused property
reads (92.6%), not a 92.6% task-speed claim. Real savings depend on the provider,
window tree and COM latency.

A separate native Windows probe used only a disposable, non-activating test
window, with before/after implementations and alternating order. It reached its
**60-second cutoff** before completing the sample set; the window was destroyed.
That measurement is incomplete and is not used as performance evidence. UIA's
per-walk budget still cannot interrupt an individual blocking COM call.

Model latency and one model decision per action remain. Prompt changes encourage
existing compound tools, but their effect on model choices has not been measured.
For a larger improvement, the next design step is bounded multi-action execution
with cancellation, per-action confirmation, stop-on-error and mandatory screen
refresh boundaries. That is a separate behavior change, not part of this patch.

## Reproduce the checks

From the project root, using the Python environment that has the app's existing
dependencies installed (PowerShell syntax):

```powershell
python -m tests.test_task_speed
python -m pytest -q -p no:cacheprovider --basetemp="$env:TEMP/jarvis_speed_focused" tests/test_task_speed.py tests/test_latency.py tests/test_yolo_mode.py tests/test_action_space.py tests/test_os_control.py tests/test_stop_cancellation.py tests/test_stop_session.py
python -m pytest -q -p no:cacheprovider --basetemp="$env:TEMP/jarvis_speed_regression" --ignore=tests/test_live_vision.py
```

`tests.test_task_speed` prints individual timing samples and property-read counts.
It mocks COM and desktop access and does not read user memory, capture the screen,
activate the webcam or contact a model.

Results obtained:

- Existing focused baseline: **110 passed**.
- New regressions before the fix: **6 failed, 3 passed**, exposing the extra work.
- Focused suite after the fix: **119 passed**.
- Broad regression: **1,686 passed, 1 skipped, 8 subtests passed** in 91.49 s.
- The entire `tests/test_live_vision.py` file was excluded from the broad run
  because some tests capture the real desktop or activate the webcam. No claim
  is made about a full hardware-backed live-vision run.
- The model-visible action-schema JSON snapshot is byte-for-byte unchanged.

Remaining validation: time a user-selected task in the affected frontend, with
its actual model and app, before claiming a whole-task latency improvement.
