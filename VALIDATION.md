# Validation report

Version: NS Energy Twin Lab v7.4

Checks completed:

- Python syntax compilation for all source modules.
- Twenty functional tests covering twin initialization, complete service
  chains, shared external demand, reproducibility, both consolidation modes,
  slice inactivity, steady slice population, VNF identity preservation, and
  URLLC migration continuity. The new tests also verify placement-aware
  Transport routes, shared offered traffic, and backward-compatible history
  fields, and zero Transport traffic for zero-user slices.
- Checkpoint CSV schemas remain stable when different action types appear in
  different checkpoints.
- A controlled 1,000-second batch run with 153 initial VNFs, seed 20260720,
  steady workload, and 50-second checkpoints.
- Exactly 1,000 unique history timestamps per scenario and 1,000 shared
  workload observations were written.
- `offered_users` and `served_users` were identical between the independent
  twins at every simulated instant.
- Offered traffic was identical in both twins at every instant. Hop-weighted
  traffic and Transport power diverged only after scenario placement diverged.
- Mean Transport power was 79.10 W for the heuristic and 81.99 W for the
  proposed mechanism in this validation run. This increase is a legitimate
  placement result; no Transport saving is imposed.
- The batch runner completed with valid status and configuration metadata.
- All action rows retained the same CSV schema; no shifted or missing action
  identifiers were found.

The reference validation selected infrastructure consolidation, VNF-instance
consolidation, scale-down, scale-in, scale-out, and no-action. No action is
forced; its selection depends on feasibility, persistent demand, and positive
projected net benefit.

The validation run ended with 29.87% cumulative energy saving. Aggregate
QoS/SLA compliance was 100% in both scenarios. These numbers validate the
software path only and are not publication results; final conclusions require
multiple independent seeds and statistical reporting.
