# Mathematical correspondence with the article

| Equation / concept | Implementation |
|---|---|
| Node resource load | `node_load()` |
| Capacity constraint | `capacity_feasible()` |
| Resource utilization | `resource_utilization()` |
| Weighted node utilization | `node_utilization()` |
| Active/sleep node power | `node_power()` |
| E2E power | `total_power()` |
| Candidate actions | `_candidate()` |
| Power saving | `before - after` |
| Adaptation cost | `optimize()` |
| QoS penalties | `qos_penalty()` |
| Normalized action score | `optimize()` |
| Positive argmax or no-action | `optimize()` |
| Smoothed persistent trigger | `_decision_peak()` and `_persistent_targets()` |
| Horizon-aware net benefit | `optimize()` |
| Minimum action dwell time | `_action_cooldown()` |
| Consolidation capacity reserve | `_reserve_feasible()` |
| VNF instance consolidation | `_effective_demand()` and `_candidate(..., "instance_consolidation", ...)` |
| Infrastructure consolidation | `_candidate(..., "infrastructure_consolidation", ...)` |

VNF instance consolidation preserves both IDs and applies a 20% reduction to the compatible pair's combined effective demand. Capacity, placement compatibility, migration cost, and QoS preservation are evaluated before the action is committed.

Infrastructure consolidation evacuates a complete underutilized Edge/Core node and applies sleep power only when all resident VNFs have feasible compatible destinations. The action score uses the same power-saving, adaptation-cost, and QoS equations as the other candidates.
