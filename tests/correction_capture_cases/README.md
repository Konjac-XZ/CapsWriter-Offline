# Correction Capture Cases

This directory contains regression cases collected from real correction-capture
failures. The cases are the long-lived specification for this behavior: the
implementation may change completely, but previously accepted cases must keep
producing their human-reviewed results.

This is a test-case-driven workflow, not a requirement to design the internal
algorithm with classic unit-test-first TDD. A case normally starts when daily
use reveals an unreasonable captured correction.

## What belongs here

Add a case here when all of the following are available:

- the text originally committed by speech input;
- the observations received while the user edited that text, in their original
  order;
- the final result reviewed by a human as correct, or an explicit expectation
  that no correction should be captured;
- enough provenance to understand where the case came from, such as the date,
  host application, and a short description of the failure.

Keep unit tests for thresholds, range mapping, queues, protocol encoding, and
other implementation details in their existing module-level test files. Cases
in this directory should describe externally meaningful input and output, not
lock down a particular algorithm.

## Adding a case

1. Preserve the original observations before changing the implementation. Do
   not reconstruct a cleaner event sequence after seeing the desired result.
2. Remove private content, credentials, and unrelated document text while
   preserving the boundaries and edits that caused the failure.
3. Add the case to the nearest host-specific Python test file. Create
   `test_<host>_cases.py` if no suitable file exists.
4. Use a descriptive `pytest.param` ID containing the observation date and the
   visible symptom. Keep the committed input, ordered observations, and
   expected output together.
5. Set the expectation to the result accepted by a human, not to the current
   algorithm's output. Run the focused test and confirm that a newly reported
   bug fails before implementing its fix.
6. Fix the implementation without weakening or deleting older cases. Change an
   existing expected result only when the earlier human judgment was wrong, and
   explain that correction in the change description.
7. Run this whole directory after the focused case passes, followed by the
   related TSF and history tests.

The simplest case shape is deliberately ordinary Python:

```python
pytest.param(
    "text originally committed by speech input",
    [
        "first observed edit",
        "later observed edit",
        "final observed edit",
    ],
    "human-reviewed final correction",
    id="2026-08-10-host-visible-symptom",
)
```

Copy the nearest existing case when possible. Do not introduce a separate data
format or parser merely to add cases. If a failure requires richer input, write
a focused Python test in this directory and keep the assertion at the most
external deterministic boundary available, preferably the final correction
event rather than an alignment score or private tracker state.

## Assertions and determinism

Prefer assertions about the final captured correction. Avoid asserting which
reconciliation mode ran, exact similarity scores, private attributes, or the
number of internal helper calls unless the reported failure specifically
depends on that contract.

Cases must be deterministic and isolated. Do not connect to a live TSF pipe,
launch a GUI, use the network, read the user's persistent state, or wait on real
debounce delays. Use fake brokers, temporary state, and controlled time when a
case needs the bridge or persistence layers.

## Running the cases

Run only the real-world correction cases while iterating:

```bash
uv run pytest -q tests/correction_capture_cases
```

Then run the directly related regression tests:

```bash
uv run pytest -q \
  tests/correction_capture_cases \
  tests/test_tsf_text_reconciler.py \
  tests/test_tsf_ipc_bridge.py \
  tests/test_finalized_history.py \
  tests/test_personalization_store.py
```

Before handing off a broader change, also run the repository checks required by
the root `AGENTS.md`.
