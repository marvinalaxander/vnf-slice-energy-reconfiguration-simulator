# NS Energy Twin Lab v7.4 · Route-Aware Transport

## Version 7.4 Transport update

- The workload generator, candidate actions, feasibility checks, decision
  score, cooldowns, and QoS/SLA logic remain unchanged from v7.3.
- Transport now depends on VNF placement along each service chain.
- Offered traffic is recorded once, while carried traffic is weighted by the
  logical hops between RAN, Edge, and Core processing locations.
- Co-located consecutive functions add no Transport hop, RAN--Edge and
  Edge--Core add one hop, and RAN--Core adds two hops.
- `history.csv` preserves every previous column and adds
  `transport_traffic_mbps`, `transport_hop_mbps`, `transport_avg_hops`, and
  `active_transport_links`.
- The lightweight dashboard preserves the prior figures and adds a Transport
  tab for power and hop-weighted traffic.

Interactive Streamlit simulator comparing a conventional heuristic with the proposed mechanism under the same infrastructure, slices, VNFs, users, demand, and external events.

## Version 7.3 experimental-execution update

- The decision mechanism, candidate actions, feasibility rules, scores,
  cooldowns, and QoS/SLA checks remain unchanged.
- The default `steady` workload uses mean-reverting LOW, NORMAL, HIGH, and
  BURST regimes whose expected long-run multiplier is approximately one.
- The active slice population does not grow without bound. A new request
  replaces a slice only after sustained zero demand terminates it.
- `growth` mode remains available for experiments that explicitly study net
  slice arrivals.
- External offered users are generated in an independent workload state and
  copied to both scenarios. `offered_users` and `served_users` are exported
  separately.
- `run_experiment.py` executes without Streamlit, writes durable checkpoints,
  and releases exported rows from memory.
- `results_dashboard.py` is a read-only, low-frequency monitor. It never
  advances the simulation or changes a decision.
- The new `workload.csv`, `configuration.json`, and `status.json` files make
  the workload and execution status directly auditable.

## Version 7.2 stochastic-demand update

- Slice demand evolves through LOW, NORMAL, HIGH, and BURST operating regimes.
- Regime transitions, dwell times, gradual ramps, short shocks, and slice
  inter-arrival times are stochastic rather than fixed.
- A fixed seed reproduces the complete external workload for scientific
  comparison.
- The heuristic and proposed mechanism receive exactly the same exogenous
  workload; neither scenario influences the demand generator.
- Actions are never scheduled or forced by the workload generator. They are
  still selected only by the mechanism's feasibility and positive-benefit
  evaluation.
- A LOW regime may naturally reach zero users. Slice termination remains based
  on persistent observed inactivity, never on a predetermined lifetime.
- Added configurable mean regime duration and demand-volatility controls.
- Preserved the v7.1 zero-user dashboard fix.

## Version 7 lifecycle update

- Slices no longer receive a random expiration time.
- A slice remains active while it has demand, becomes inactive after sustained zero demand, and is terminated only after the configurable inactivity threshold.
- Automatic independent VNF arrivals were removed.
- Every new slice request creates its service-specific initial VNF chain.
- Additional VNF instances are created by the mechanism through scale-out and removed through scale-in.
- New slice requests use a configurable mean inter-arrival interval instead of a fixed probability per second.
- Request selection is weighted by the observed service-demand pressure and is applied identically to both independent twins.
- Time continues to be displayed and exported in simulated seconds.
- Batch execution now exports history, actions, evaluated candidates, QoS observations, events, and final VNF states.
- Each decision window evaluates the four highest-priority targets per action by default, preventing interface stalls as the VNF population grows while preserving all candidate action types.

## Version 6.2 stability update

- Monitoring remains at one-second resolution, while the default decision interval is 10 seconds and remains configurable.
- Candidate conditions must persist for three decision windows before automatic reconfiguration.
- An exponential moving average with $\alpha=0.20$ filters short demand spikes.
- Per-VNF action cooldowns prevent immediate repeated or inverse reconfigurations.
- Consolidation keeps 15% destination capacity in reserve.
- Decisions evaluate projected energy benefit over a 120-second horizon and include migration, reconfiguration, switching, QoS, and recent-action penalties.
- The heuristic is never used as an input to the mechanism; it remains an independent comparison scenario.

## Version 6.1 consolidation update

- Replaced infrastructure/workload consolidation with **VNF instance consolidation**.
- Consolidation pairs two compatible instances of the same function.
- Both VNF identifiers remain available and independently manageable.
- A valid pair receives a configurable 20% reduction in combined effective demand.
- Migration, termination, or scale-in of one member safely dissolves the pair; the surviving VNF continues operating.
- E2E savings are still measured from the resulting infrastructure power and are not forced to equal 20%.
- The complete dashboard, labels, tables, events, decisions, and CSV exports are in English.
- Existing graphs and dashboard organization are preserved.
- Added **infrastructure consolidation** as a second internal mode of the same top-level consolidation action.
- Infrastructure consolidation evaluates the complete evacuation of an underutilized Edge/Core node, including all migration costs, destination load, QoS, and node sleep power.
- Action-specific candidate generation now evaluates scale-up/out, scale-down, scale-in, migration, and both consolidation modes from their corresponding operational conditions.

## Experiment

- **Heuristic:** conventional first-fit placement, energy calculation, and QoS monitoring.
- **Proposed mechanism:** evaluates scale-up, scale-down, scale-out, scale-in, migration, two internal consolidation modes, and no-action.
- Identical exogenous demand is applied to both twins.
- RAN is monitored through radio/PRB load, Transport through placement-aware
  hop-weighted traffic, and Edge/Core through computing load.
- QoS/SLA is evaluated per slice for latency, jitter, packet loss, and throughput.

## Run on macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

This is the fully interactive mode. It is useful for demonstrations and manual
events, but it continuously redraws the interface.

## Recommended lightweight experiment

```bash
python3 run_experiment.py --steps 3600 --vnfs 153 --seed 20260720 \
  --slice-request-interval 120 --slice-inactivity 120 \
  --demand-regime-interval 90 --demand-volatility 0.75 \
  --workload-mode steady --checkpoint-interval 50 \
  --pace 0.05 --output results/run_01
```

`--pace` inserts a short rest proportional to the simulated interval. Increase
it to `0.10` if the computer becomes warm. The simulated results do not change,
because the random seed and simulated clock are independent of wall-clock
execution speed.

Stopping with `Control+C` preserves all completed checkpoints. Always choose a
new output directory for another seed or repetition.

## Optional lightweight dashboard

While the command above is running, open a second Visual Studio Code terminal:

```bash
source .venv/bin/activate
python3 -m streamlit run results_dashboard.py -- \
  --results results/run_01
```

The monitor refreshes every five seconds and reads only the compact history,
workload, and action files. The large QoS and candidate logs remain on disk for
final analysis.

## Scientific-use note

The code implements the mathematical decision structure. Equipment power, capacities, resource weights, migration overhead coefficients, traffic profiles, and the 20% instance-consolidation reduction are configurable experimental parameters. They must be documented and supported through the published E2E energy model, infrastructure measurements, literature, or sensitivity analysis before final publication.
