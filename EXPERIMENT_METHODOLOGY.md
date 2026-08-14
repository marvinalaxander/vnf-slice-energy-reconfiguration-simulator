# Implemented experimental methodology

## Compared scenarios

Both twins start from the same infrastructure, slices, VNFs, users, and demand. The heuristic keeps conventional placement, while the proposed mechanism evaluates six adaptive actions plus no-action.

## Segment-aware energy representation

| Segment | Operational load | Energy representation |
|---|---|---|
| RAN | Equivalent radio/PRB load | Active/sleep radio equipment and load-dependent power |
| Edge | Aggregated computing load | Idle-to-maximum power of active Edge nodes |
| Transport | Placement-aware E2E traffic | Aggregate-link power driven by traffic multiplied by logical intersegment hops |
| Core | Aggregated computing load | Idle-to-maximum power of active Core nodes |

For slice \(s\), the simulator follows the canonical VNF chain and computes
the expected number of logical hops \(h_s(t)\) from the bandwidth-weighted
placement of each processing stage. Consecutive functions in the same segment
add no Transport hop; RAN--Edge and Edge--Core add one hop; and RAN--Core adds
two hops. Horizontally scaled instances contribute according to their share of
the stage bandwidth demand.

The Transport load is:

$$
L_{tr}(t)=\sum_s B_s(t)h_s(t),
$$

and aggregate Transport power is:

$$
P_{tr}(t)=P_{tr}^{idle}+
\left(P_{tr}^{max}-P_{tr}^{idle}\right)
\min\left(1,\frac{L_{tr}(t)}{C_{tr}}\right).
$$

The two twins receive the same offered traffic \(B_s(t)\), but their Transport
loads may differ because placement, migration, elasticity, and consolidation
can produce different routes. No fixed Transport saving is imposed.

## VNF instance consolidation

Consolidation pairs two active, compatible VNF instances of the same function. Both IDs remain in the experiment. For a reciprocal valid pair:

$$
D^{con}_{i,j}(t)=(1-\eta_{con})(D_i(t)+D_j(t)),\qquad \eta_{con}=0.20.
$$

The 20% value applies only to the pair's combined effective demand. It is not imposed on total E2E power. The realized E2E saving is calculated after accounting for node idle power, dynamic power, migration energy, and the remaining infrastructure.

A pair is safely dissolved if one member migrates, is terminated, or is removed through scale-in. The other identifier and VNF remain operational.

## Infrastructure consolidation

This second consolidation mode searches for underutilized Edge/Core nodes. It evaluates the coordinated migration of every resident VNF to compatible active destinations. The source node is released to sleep mode only after it becomes empty and only if capacity and QoS/SLA remain feasible.

$$
B_{infra}=P(t)-P^{infra}(t+1)-\sum_{v\in V_n}E_{mig,v}-C_{rec}-C_{sw}-\pi_{QoS}.
$$

Both modes remain part of the single top-level consolidation action. CSV records distinguish them through `consolidation_mode` and report `released_nodes`.

## Migration energy

Migration duration depends on state size and available transport bandwidth:

$$
T_{mig}=\frac{8\times1024\times S_{GB}}{B_{available,Mbps}}.
$$

The current migration-overhead coefficients are configurable experimental assumptions and require calibration or sensitivity analysis.

## Result interpretation

Report separately: the configured 20% instance-demand reduction, measured E2E saving, affected-segment saving, migration energy, number of consolidated pairs, infrastructure nodes released, and QoS/SLA compliance. These quantities must not be presented as equivalent percentages.

## Stable event-triggered decision controller

The simulator monitors demand every second but evaluates automatic actions every 10 seconds by default. A condition must persist for three consecutive decision windows. Decision demand is filtered using an exponential moving average with $\alpha=0.20$.

The projected net benefit over horizon $H=120$ seconds is:

$$
S_H(a,t)=\frac{\Delta P(a,t)H}{3600}-E_{adapt}(a,t)-C_{churn}(a,t)+\lambda_Q\Delta QoS(a,t).
$$

An action is committed only when $S_H(a,t)>0$, capacity and QoS remain feasible, and the VNF cooldown has elapsed. Consolidation destinations retain a 15% capacity reserve. These controls prevent reconfiguration thrashing without comparing the proposed mechanism against the heuristic during decision-making.

## Demand-driven slice lifecycle

No slice is assigned a predetermined or randomly sampled lifetime. New slice
requests arrive through an exogenous request process configured by a mean
inter-arrival interval. Each admitted request creates the complete initial VNF
chain associated with its service type. The same request, service class, user
population, and demand trajectory are supplied to both independent twins.

An existing slice retains its identity when demand decreases. Additional VNF
instances can only be created by scale-out, while redundant instances can be
removed by scale-in. A slice becomes inactive only after its observed user
demand remains at zero for the configured inactivity window. If zero demand
persists until the termination threshold, its remaining VNFs are released and
the slice is terminated. Thus, termination is caused by sustained inactivity
rather than by age.

## Reproducible stochastic workload

Each slice follows a stochastic regime process with LOW, NORMAL, HIGH, and
BURST states. State durations are sampled from a log-normal distribution and
transitions follow a Markov matrix. Within a regime, demand changes gradually
through mean reversion, a low-amplitude periodic component, random noise, and
rare positive or negative shocks. New slice inter-arrival times remain
exponentially distributed.

The generator creates operational conditions but never selects an adaptation
action. Scale-up, scale-down, scale-out, scale-in, migration, VNF-instance
consolidation, infrastructure consolidation, and no-action continue to compete
through the same feasibility and net-benefit equations. The random seed is
exportable and makes the full exogenous workload reproducible. Both twins
receive the same realization.

Migration and consolidation candidates receive an additional URLLC resilience
check: they cannot increase the number of segment transitions in a
latency-critical service chain. This check prevents energy savings from being
obtained by increasing the structural latency of an URLLC placement.

## Steady-workload execution

Version 7.3 uses a mean-reverting regime transition matrix and regime
multipliers whose expected long-run value is approximately one. Consequently,
aggregate demand fluctuates around its initial operating level rather than
being configured to increase monotonically. Local slices can still enter LOW,
NORMAL, HIGH, or BURST states, so elasticity, migration, and consolidation
conditions emerge from heterogeneous workload trajectories.

The default steady mode holds the target slice population at its initial
value. It does not assign a predetermined lifetime to any slice. A slice is
terminated only after observed zero demand persists for the configured
inactivity threshold; a later external request may replace the released
service. Growth mode remains available as a separate experimental treatment.

Offered demand is generated outside both scenario states and copied to the
heuristic and proposed mechanism at every simulated instant. The history
therefore exports `offered_users` separately from `served_users`, enabling the
researcher to verify that differences in energy are not caused by unequal
exogenous workloads.

The recommended batch runner performs the same one-second simulation steps but
does not render the Streamlit interface. It writes history, workload, actions,
candidates, QoS, and events at fixed checkpoints. The optional results monitor
only reads these files and cannot alter the simulation state or action
selection.
