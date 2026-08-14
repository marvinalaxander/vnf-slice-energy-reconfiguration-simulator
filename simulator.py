from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from catalog import SLICE_CHAINS, SLICE_QOS, VNF_PROFILES
from models import NetworkSlice, Node, RESOURCES, ScenarioState, VNF


SEGMENT_WEIGHTS = {
    "RAN": {"cpu": .42, "ram": .13, "storage": .05, "bandwidth": .40},
    "EDGE": {"cpu": .43, "ram": .27, "storage": .12, "bandwidth": .18},
    "TRANSPORT": {"cpu": .12, "ram": .08, "storage": .02, "bandwidth": .78},
    "CORE": {"cpu": .38, "ram": .27, "storage": .22, "bandwidth": .13},
}

SERVICE_USERS = {"eMBB": (350, 1100), "URLLC": (80, 360), "mMTC": (900, 4200)}
SERVICE_REFERENCE = {"eMBB": 650, "URLLC": 190, "mMTC": 1900}
DEMAND_REGIME_MULTIPLIERS = {
    # Together with the transition matrix, these ranges have an expected
    # multiplier close to 1.0. Aggregate demand therefore fluctuates around
    # the initial level instead of drifting upward or downward.
    "LOW": (0.25, 0.65),
    "NORMAL": (0.85, 1.15),
    "HIGH": (1.25, 1.75),
    "BURST": (1.70, 2.30),
}
DEMAND_TRANSITIONS = {
    # The transition matrix is mean-reverting. It allows peaks and low-demand
    # periods without introducing a systematic upward trend.
    "LOW": (("LOW", .42), ("NORMAL", .55), ("HIGH", .03)),
    "NORMAL": (("LOW", .22), ("NORMAL", .56), ("HIGH", .20), ("BURST", .02)),
    "HIGH": (("LOW", .04), ("NORMAL", .56), ("HIGH", .35), ("BURST", .05)),
    "BURST": (("NORMAL", .45), ("HIGH", .50), ("BURST", .05)),
}


@dataclass
class Candidate:
    action: str
    target: str
    state: ScenarioState
    power_before: float
    power_after: float
    saving: float
    migration_cost: float
    reconfiguration_cost: float
    switching_cost: float
    qos_penalty: float
    score: float = 0.0
    reason: str = ""
    feasible: bool = True
    segment_saving_pct: float = 0.0
    consolidation_partner: str = ""
    instance_saving_pct: float = 0.0
    consolidation_mode: str = ""
    released_nodes: int = 0
    affected_vnfs: Tuple[str, ...] = ()
    projected_net_benefit_wh: float = 0.0


@dataclass
class TwinSimulation:
    seed: int = 20260720
    initial_vnfs: int = 153
    decision_interval: int = 10
    optimization_start: int = 30
    instance_consolidation_saving: float = 0.20
    decision_horizon_s: int = 120
    demand_ema_alpha: float = 0.20
    persistence_windows: int = 3
    consolidation_reserve: float = 0.15
    max_candidates_per_action: int = 4
    mean_slice_request_interval_s: int = 120
    slice_inactive_after_s: int = 60
    slice_terminate_after_s: int = 120
    demand_regime_mean_s: int = 90
    demand_volatility: float = 0.75
    workload_mode: str = "steady"
    target_active_slices: Optional[int] = None

    def __post_init__(self):
        self.rng = np.random.default_rng(self.seed)
        self.next_vnf_number = 1
        self.next_slice_number = 1
        self.manual_overrides: Dict[str, dict] = {}
        initial = self._build_initial_state()
        self.baseline = initial.clone("Heuristic")
        self.mechanism = initial.clone("Proposed mechanism")
        self.event_log: List[dict] = []
        self.candidate_log: List[dict] = []
        self.qos_history: List[dict] = []
        self.workload_history: List[dict] = []
        if self.target_active_slices is None:
            self.target_active_slices = len(self.baseline.slices)
        self.next_slice_request_time = self._sample_next_slice_request(0)
        self.decision_load_ema: Dict[str, float] = {}
        self.condition_persistence: Dict[Tuple[str, str], int] = {}
        self.last_action_time: Dict[str, int] = {}
        self.last_action_kind: Dict[str, str] = {}
        self.slice_demand_profiles: Dict[str, dict] = {}
        for ns in self.baseline.slices.values():
            self._register_demand_profile(ns.id, ns.service, ns.users, initial=True)

    # ---------- infrastructure and initial configuration ----------
    def _nodes(self) -> Dict[str, Node]:
        specs = {
            # RAN uses radio-load units (PRB-equivalent) for power, while the
            # virtual resources of vDU/vCU remain internal placement constraints.
            "RAN": (5, {"cpu": 96, "ram": 192, "storage": 650, "bandwidth": 4200}, 760, 1780, 45),
            "EDGE": (8, {"cpu": 128, "ram": 320, "storage": 1600, "bandwidth": 300}, 195, 550, 12),
            # One aggregate E2E transport path; its load is carried traffic.
            "TRANSPORT": (1, {"cpu": 1, "ram": 1, "storage": 1, "bandwidth": 24000}, 65, 190, 18),
            "CORE": (8, {"cpu": 160, "ram": 512, "storage": 3200, "bandwidth": 420}, 95, 300, 16),
        }
        nodes = {}
        for segment, (count, cap, idle, maximum, sleep) in specs.items():
            for i in range(1, count + 1):
                nid = f"{segment}-{i:02d}"
                nodes[nid] = Node(nid, segment, dict(cap), idle, maximum, sleep, True)
        return nodes

    def _build_initial_state(self) -> ScenarioState:
        state = ScenarioState("initial", self._nodes(), {}, {})
        attempts = 0
        while len(state.vnfs) < self.initial_vnfs and attempts < self.initial_vnfs * 5:
            service = ("eMBB", "URLLC", "mMTC")[(self.next_slice_number - 1) % 3]
            if len(state.vnfs) + len(SLICE_CHAINS[service]) > self.initial_vnfs:
                break
            sid = self._new_slice_id()
            low, high = SERVICE_USERS[service]
            users = int(self.rng.integers(low, high + 1))
            ns = NetworkSlice(sid, service, dict(SLICE_QOS[service]), 1.0, users, users)
            state.slices[sid] = ns
            for profile in SLICE_CHAINS[service]:
                v = self._new_vnf(ns, profile)
                if self._place_balanced(state, v):
                    state.vnfs[v.id] = v
                    ns.vnf_ids.append(v.id)
            attempts += 1
        # Preserve the requested initial VNF count without leaving a partial
        # service chain. Any remainder represents already-scaled instances of
        # functions belonging to otherwise complete slices.
        sources = list(state.vnfs.values())
        source_index = 0
        while len(state.vnfs) < self.initial_vnfs and sources:
            source = sources[source_index % len(sources)]
            source_index += 1
            clone = source.clone()
            clone.id = self._new_vnf_id()
            clone.node_id = ""
            clone.segment = ""
            clone.consolidated_with = ""
            peers = [v for v in state.vnfs.values()
                     if v.slice_id == source.slice_id and v.profile == source.profile]
            clone.instance = max(v.instance for v in peers) + 1
            if self._place_balanced(state, clone):
                state.vnfs[clone.id] = clone
                state.slices[clone.slice_id].vnf_ids.append(clone.id)
            elif source_index > len(sources) * 3:
                break
        self._update_demands_for_state(state)
        self._refresh_nodes(state, allow_sleep=False)
        return state

    def _new_slice_id(self):
        sid = f"NS-{self.next_slice_number:04d}"
        self.next_slice_number += 1
        return sid

    def _new_vnf_id(self):
        vid = f"VNF-{self.next_vnf_number:05d}"
        self.next_vnf_number += 1
        return vid

    def _sample_next_slice_request(self, current_time):
        """Schedule an exogenous request without a per-step Bernoulli probability."""
        interval = max(1, int(round(self.rng.exponential(self.mean_slice_request_interval_s))))
        return int(current_time) + interval

    def _sample_regime_duration(self):
        """Random positive dwell time; the seed makes complete runs reproducible."""
        mean = max(10.0, float(self.demand_regime_mean_s))
        sigma = .55
        mu = np.log(mean) - .5 * sigma**2
        return max(10, int(round(self.rng.lognormal(mu, sigma))))

    def _register_demand_profile(self, slice_id, service, users, initial=False):
        if slice_id in self.slice_demand_profiles:
            return
        initial_regime = str(self.rng.choice(
            ("LOW", "NORMAL", "HIGH"),
            p=(.12, .70, .18) if initial else (.08, .72, .20),
        ))
        self.slice_demand_profiles[slice_id] = {
            "base_users": max(1, int(users)),
            "regime": initial_regime,
            "next_change": self.baseline.time + self._sample_regime_duration(),
            "target_users": max(0, int(users)),
            "current_users": max(0, int(users)),
        }

    def _next_regime(self, current):
        transitions = DEMAND_TRANSITIONS[current]
        labels = [x[0] for x in transitions]
        probabilities = np.asarray([x[1] for x in transitions], dtype=float)
        probabilities /= probabilities.sum()
        return str(self.rng.choice(labels, p=probabilities))

    def _enter_demand_regime(self, slice_id, profile):
        previous = profile["regime"]
        regime = self._next_regime(previous)
        profile["regime"] = regime
        profile["next_change"] = self.baseline.time + self._sample_regime_duration()
        low, high = DEMAND_REGIME_MULTIPLIERS[regime]
        multiplier = float(self.rng.uniform(low, high))
        # A genuinely idle service is possible only from the LOW regime. It is
        # an observed demand outcome, not a predetermined slice lifetime.
        if regime == "LOW" and self.rng.random() < .18:
            target = 0
        else:
            target = max(1, int(round(profile["base_users"] * multiplier)))
        profile["target_users"] = target
        self.event_log.append({
            "time": self.baseline.time,
            "event": "DEMAND_REGIME",
            "target": slice_id,
            "detail": f"{previous}->{regime}, target {target} users",
        })

    def _sample(self, bounds):
        return float(self.rng.uniform(*bounds))

    def _new_vnf(self, ns, profile_name, forced_id=None, instance=1):
        p = VNF_PROFILES[profile_name]
        allocated = {"cpu": self._sample(p.cpu_range), "ram": self._sample(p.ram_range),
                     "storage": self._sample(p.storage_range), "bandwidth": self._sample(p.bandwidth_range)}
        demand = {r: allocated[r] * .55 for r in RESOURCES}
        return VNF(forced_id or self._new_vnf_id(), ns.id, profile_name, p.allowed_segments, "", "", allocated, demand,
                   min(p.latency_ms, ns.qos["latency_ms"]), self._sample(p.state_gb_range),
                   3 if ns.service == "URLLC" else 2 if ns.service == "eMBB" else 1,
                   p.migratable, p.scalable_vertical, p.scalable_horizontal, instance)

    # ---------- Equations (1)-(7): load, capacity, utilization and power ----------
    def node_load(self, state, node_id, allocated=False, exclude=None):
        # Eq. (1): L^r_n(t) = sum d^r_v(t)
        load = {r: 0.0 for r in RESOURCES}
        for v in state.vnfs.values():
            if v.active and v.node_id == node_id and v.id != exclude:
                values = v.allocated if allocated else self._effective_demand(state, v)
                for r in RESOURCES:
                    load[r] += values[r]
        return load

    def _is_valid_consolidated_pair(self, state, v):
        peer = state.vnfs.get(v.consolidated_with) if v.consolidated_with else None
        return bool(peer and peer.active and peer.consolidated_with == v.id and
                    peer.profile == v.profile and peer.node_id == v.node_id)

    def _effective_demand(self, state, v):
        """Apply the configured 20% reduction only to a valid VNF pair."""
        factor = 1.0 - self.instance_consolidation_saving if self._is_valid_consolidated_pair(state, v) else 1.0
        return {r: v.demand[r] * factor for r in RESOURCES}

    def _unlink_consolidation(self, state, vnf_id):
        v = state.vnfs.get(vnf_id)
        if not v or not v.consolidated_with:
            return
        peer = state.vnfs.get(v.consolidated_with)
        if peer and peer.consolidated_with == v.id:
            peer.consolidated_with = ""
        v.consolidated_with = ""

    def capacity_feasible(self, state):
        # Eq. (2): L^r_n(t) <= C^r_n
        return all(self.node_load(state, n.id)[r] <= n.capacity[r] + 1e-9
                   for n in state.nodes.values() for r in RESOURCES)

    def resource_utilization(self, state, node_id):
        # Eq. (3): U^r_n(t) = L^r_n(t) / C^r_n
        n = state.nodes[node_id]
        load = self.node_load(state, node_id)
        return {r: load[r] / max(n.capacity[r], 1e-9) for r in RESOURCES}

    def radio_load_units(self, state):
        return sum(ns.users * {"eMBB":1.0,"URLLC":.65,"mMTC":.08}[ns.service] for ns in state.slices.values())

    def transport_traffic_mbps(self, state):
        # Offered traffic is exogenous and therefore identical in both twins.
        # It is recorded separately from hop-weighted carried traffic.
        return sum(
            0.0 if ns.users <= 0 else
            ns.qos["throughput_mbps"] * np.clip(ns.demand_factor, .2, 2.4)
            for ns in state.slices.values()
        )

    @staticmethod
    def _transport_hops_between(source_segment, destination_segment):
        """Logical Transport hops between consecutive processing locations."""
        if source_segment == destination_segment:
            return 0.0
        # RAN--EDGE and EDGE--CORE are adjacent. RAN--CORE crosses both
        # logical Transport stages. A Transport-hosted forwarding function is
        # treated as one hop from any processing segment.
        if {source_segment, destination_segment} == {"RAN", "CORE"}:
            return 2.0
        return 1.0

    def _stage_segment_distribution(self, state, ns, profile):
        """Bandwidth-weighted placement distribution for one VNF-chain stage."""
        instances = [
            state.vnfs[vid] for vid in ns.vnf_ids
            if vid in state.vnfs and state.vnfs[vid].active
            and state.vnfs[vid].profile == profile
        ]
        if not instances:
            return {}
        weights = {}
        for v in instances:
            weight = max(.001, self._effective_demand(state, v)["bandwidth"])
            weights[v.segment] = weights.get(v.segment, 0.0) + weight
        total = sum(weights.values()) or 1.0
        return {segment: weight / total for segment, weight in weights.items()}

    def slice_transport_metrics(self, state, ns):
        """Offered traffic and expected logical hops for one network slice."""
        traffic = (
            0.0 if ns.users <= 0 else
            ns.qos["throughput_mbps"] * np.clip(ns.demand_factor, .2, 2.4)
        )
        previous = {"RAN": 1.0}
        hops = 0.0
        for profile in SLICE_CHAINS[ns.service]:
            current = self._stage_segment_distribution(state, ns, profile)
            if not current:
                continue
            hops += sum(
                source_weight * destination_weight
                * self._transport_hops_between(source, destination)
                for source, source_weight in previous.items()
                for destination, destination_weight in current.items()
            )
            previous = current
        return {
            "traffic_mbps": float(traffic),
            "average_hops": float(hops),
            "hop_mbps": float(traffic * hops),
        }

    def transport_route_metrics(self, state):
        """Aggregate placement-aware Transport metrics for one scenario."""
        per_slice = {
            ns.id: self.slice_transport_metrics(state, ns)
            for ns in state.slices.values()
        }
        traffic = sum(values["traffic_mbps"] for values in per_slice.values())
        hop_traffic = sum(values["hop_mbps"] for values in per_slice.values())
        return {
            "traffic_mbps": float(traffic),
            "hop_mbps": float(hop_traffic),
            "average_hops": float(hop_traffic / traffic) if traffic else 0.0,
            "active_links": int(hop_traffic > 0),
            "per_slice": per_slice,
        }

    def transport_hop_traffic_mbps(self, state):
        return self.transport_route_metrics(state)["hop_mbps"]

    def node_utilization(self, state, node_id):
        # Eq. (4): U_n(t) = sum alpha_{s(n),r} U^r_n(t)
        n = state.nodes[node_id]
        if n.segment == "RAN":
            active = max(1,sum(x.active for x in state.nodes.values() if x.segment=="RAN"))
            return min(1.0,self.radio_load_units(state)/(active*n.capacity["bandwidth"]))
        if n.segment == "TRANSPORT":
            return min(1.0,self.transport_hop_traffic_mbps(state)/n.capacity["bandwidth"])
        u = self.resource_utilization(state, node_id)
        return sum(SEGMENT_WEIGHTS[n.segment][r] * u[r] for r in RESOURCES)

    def node_power(self, state, node):
        # Eqs. (5)-(6): active linear power or sleep-state power
        if not node.active:
            return node.sleep_power_w
        u = min(1.0, self.node_utilization(state, node.id))
        return node.idle_power_w + (node.max_power_w - node.idle_power_w) * u

    def total_power(self, state):
        # Eq. (7): P(t) = sum P_n(t)
        return sum(self.node_power(state, n) for n in state.nodes.values())

    def segment_power(self, state):
        result = {s: 0.0 for s in ("RAN", "EDGE", "TRANSPORT", "CORE")}
        for n in state.nodes.values():
            result[n.segment] += self.node_power(state, n)
        return result

    # ---------- dynamic users -> slice traffic -> VNF demand ----------
    def _profile_sensitivity(self, profile, resource):
        values = {
            "vDU": (1.18, .70, .30, 1.18), "vCU": (1.10, .82, .38, 1.05),
            "UPF": (1.05, .66, .30, 1.25), "Cache": (.78, 1.22, 1.30, 1.05),
            "Firewall": (.95, .70, .25, 1.12), "IDS_IPS": (1.22, .92, .62, 1.10),
            "LoadBalancer": (.88, .62, .20, 1.20), "IoTGateway": (1.02, .82, .55, .82),
            "AMF": (.74, .85, .40, .52), "SMF": (.78, .82, .42, .55),
            "UDM": (.66, 1.02, 1.18, .40), "AUSF": (.62, .70, .38, .35),
            "NAT": (.82, .55, .20, 1.15), "vRouter": (.70, .45, .18, 1.28),
        }.get(profile, (.8, .8, .5, .8))
        return dict(zip(RESOURCES, values))[resource]

    def _update_users(self):
        # The external workload is generated independently of both scenarios
        # and then copied to each twin. Placement decisions cannot change it.
        for sid, profile in list(self.slice_demand_profiles.items()):
            baseline_slice = self.baseline.slices.get(sid)
            mechanism_slice = self.mechanism.slices.get(sid)
            if baseline_slice is None and mechanism_slice is None:
                continue
            if self.baseline.time >= profile["next_change"]:
                self._enter_demand_regime(sid, profile)
            # Once an external profile explicitly reaches zero, random noise
            # must not resurrect users before the next regime transition.
            current_users = int(profile["current_users"])
            target_users = int(profile["target_users"])
            if target_users == 0 and current_users == 0:
                new_users = 0
                for state in (self.baseline, self.mechanism):
                    if sid in state.slices:
                        state.slices[sid].users = new_users
                        state.slices[sid].target_users = target_users
                        state.slices[sid].demand_factor = 0.0
                continue
            phase = int(sid.split("-")[1]) * .37
            seasonal = .10 * self.demand_volatility * np.sin(self.baseline.time / 31 + phase)
            target = max(0.0, target_users * (1 + seasonal))
            noise = self.rng.normal(
                0, max(1.0, profile["base_users"] * .012 * self.demand_volatility)
            )
            # Mean reversion creates gradual ramps instead of instantaneous
            # jumps, while rare shocks model short-lived workload bursts.
            shock = 0.0
            if self.rng.random() < .006 * self.demand_volatility:
                shock = (
                    self.rng.choice((-1.0, 1.0))
                    * profile["base_users"]
                    * self.rng.uniform(.08, .24)
                )
            new_users = max(
                0, int(round(current_users + .13 * (target - current_users) + noise + shock))
            )
            service = baseline_slice.service if baseline_slice else mechanism_slice.service
            if target_users <= max(2, int(.005 * SERVICE_REFERENCE[service])) and new_users <= 2:
                new_users = 0
            profile["current_users"] = new_users
            for state in (self.baseline, self.mechanism):
                if sid in state.slices:
                    state.slices[sid].users = new_users
                    state.slices[sid].target_users = target_users
                    state.slices[sid].demand_factor = new_users / SERVICE_REFERENCE[service]

    def _update_demands_for_state(self, state):
        for ns in state.slices.values():
            groups = {}
            for vid in ns.vnf_ids:
                if vid in state.vnfs and state.vnfs[vid].active:
                    groups.setdefault(state.vnfs[vid].profile, []).append(state.vnfs[vid])
            for profile, instances in groups.items():
                load_factor = np.clip(ns.users / SERVICE_REFERENCE[ns.service], 0.0, 2.4)
                for v in instances:
                    for r in RESOURCES:
                        base = (v.allocated[r] * .48 * load_factor * self._profile_sensitivity(profile, r)) / len(instances)
                        v.demand[r] = float(max(0.0, base))
        # A manual stress/load definition remains active for a chosen number
        # of simulated seconds and is applied identically to both twins.
        for vid, override in self.manual_overrides.items():
            if override["until"] >= state.time and vid in state.vnfs:
                v = state.vnfs[vid]
                for r, pct in override["percent"].items():
                    v.demand[r] = v.allocated[r] * pct / 100

    # ---------- placement ----------
    def _fits(self, state, v, node, proposed=None, exclude=None, headroom=.92):
        if node.segment not in v.allowed_segments:
            return False
        used = self.node_load(state, node.id, allocated=True, exclude=exclude)
        req = proposed or v.allocated
        return all(used[r] + req[r] <= node.capacity[r] * headroom for r in RESOURCES)

    def _place_balanced(self, state, v):
        candidates = [n for n in state.nodes.values() if self._fits(state, v, n, headroom=.88)]
        if not candidates:
            return False
        candidates.sort(key=lambda n: (self.node_utilization(state, n.id), n.id))
        n = candidates[0]
        v.node_id, v.segment, n.active = n.id, n.segment, True
        return True

    def _place_first_fit(self, state, v):
        for n in sorted(state.nodes.values(), key=lambda x: x.id):
            if self._fits(state, v, n, headroom=.98):
                v.node_id, v.segment, n.active = n.id, n.segment, True
                return True
        return False

    def _place_energy(self, state, v):
        candidates = [n for n in state.nodes.values() if self._fits(state, v, n)]
        if not candidates:
            return False
        candidates.sort(key=lambda n: (not n.active, n.max_power_w, -self.node_utilization(state, n.id)))
        n = candidates[0]
        v.node_id, v.segment, n.active = n.id, n.segment, True
        return True

    # ---------- QoS model and Eqs. (11)-(12) ----------
    def slice_qos(self, state, ns):
        vnfs = [state.vnfs[v] for v in ns.vnf_ids if v in state.vnfs and state.vnfs[v].active]
        if not vnfs:
            return {"latency_ms": 999, "jitter_ms": 999, "loss_pct": 100, "throughput_mbps": 0}
        node_u = [max(self.resource_utilization(state, v.node_id).values()) for v in vnfs]
        peak = max(node_u, default=0)
        segments = [v.segment for v in vnfs]
        transitions = sum(a != b for a, b in zip(segments, segments[1:]))
        base_latency = {"eMBB": 12, "URLLC": 3.1, "mMTC": 25}[ns.service]
        queue_delay = 1.5 * peak / max(1 - min(peak, .985), .015)
        latency = base_latency + transitions * 1.7 + queue_delay
        jitter = {"eMBB": 2.0, "URLLC": .65, "mMTC": 4.0}[ns.service] + max(0, peak - .55) ** 2 * 19
        loss = max(0, peak - .78) ** 2 * 18
        capacity = sum(v.allocated["bandwidth"] for v in vnfs if v.profile in ("UPF", "vDU", "vCU", "vRouter"))
        required = ns.qos["throughput_mbps"] * np.clip(ns.demand_factor, .2, 2.4)
        throughput = min(required * 1.12, capacity * 7.5) * max(.05, 1 - max(0, peak - .82) * 1.8)
        return {"latency_ms": latency, "jitter_ms": jitter, "loss_pct": loss, "throughput_mbps": throughput}

    def qos_penalty(self, state):
        # Eqs. (11)-(12), beta values express relative SLA importance.
        penalty = 0.0
        for ns in state.slices.values():
            q = self.slice_qos(state, ns)
            priority = 1.5 if ns.service == "URLLC" else 1.15 if ns.service == "eMBB" else 1.0
            penalty += priority * max(0, q["latency_ms"] - ns.qos["latency_ms"]) / ns.qos["latency_ms"]
            penalty += .8 * priority * max(0, q["jitter_ms"] - ns.qos["jitter_ms"]) / ns.qos["jitter_ms"]
            penalty += 1.2 * priority * max(0, q["loss_pct"] - ns.qos["loss_pct"]) / ns.qos["loss_pct"]
            minimum = ns.qos["throughput_mbps"] * np.clip(ns.demand_factor, .2, 2.4)
            penalty += priority * max(0, minimum - q["throughput_mbps"]) / max(minimum, 1)
        return penalty

    def qos_feasible(self, state, tolerance=.05):
        for ns in state.slices.values():
            q = self.slice_qos(state, ns)
            if q["latency_ms"] > ns.qos["latency_ms"] * (1 + tolerance): return False
            if q["jitter_ms"] > ns.qos["jitter_ms"] * (1 + tolerance): return False
            if q["loss_pct"] > ns.qos["loss_pct"] * (1 + tolerance): return False
            minimum = ns.qos["throughput_mbps"] * np.clip(ns.demand_factor, .2, 2.4)
            if q["throughput_mbps"] < minimum * (1 - tolerance): return False
        return True

    def qos_preserved(self, before, after):
        """No candidate may create a new violation or worsen an existing one."""
        for sid, ns_before in before.slices.items():
            if sid not in after.slices:
                return False
            q0 = self.slice_qos(before, ns_before)
            ns_after = after.slices[sid]
            q1 = self.slice_qos(after, ns_after)
            min0 = ns_before.qos["throughput_mbps"] * np.clip(ns_before.demand_factor, .2, 2.4)
            min1 = ns_after.qos["throughput_mbps"] * np.clip(ns_after.demand_factor, .2, 2.4)
            severity0 = (max(0,q0["latency_ms"]/ns_before.qos["latency_ms"]-1) +
                         max(0,q0["jitter_ms"]/ns_before.qos["jitter_ms"]-1) +
                         max(0,q0["loss_pct"]/ns_before.qos["loss_pct"]-1) +
                         max(0,1-q0["throughput_mbps"]/max(min0,1)))
            severity1 = (max(0,q1["latency_ms"]/ns_after.qos["latency_ms"]-1) +
                         max(0,q1["jitter_ms"]/ns_after.qos["jitter_ms"]-1) +
                         max(0,q1["loss_pct"]/ns_after.qos["loss_pct"]-1) +
                         max(0,1-q1["throughput_mbps"]/max(min1,1)))
            if severity1 > severity0 + 1e-7:
                return False
        return True

    def urlcc_resilience_preserved(self, before, after):
        """Keep latency-critical chains from becoming more fragile."""
        for sid, ns_before in before.slices.items():
            if ns_before.service != "URLLC" or sid not in after.slices:
                continue
            before_vnfs = [before.vnfs[v] for v in ns_before.vnf_ids if v in before.vnfs]
            ns_after = after.slices[sid]
            after_vnfs = [after.vnfs[v] for v in ns_after.vnf_ids if v in after.vnfs]
            before_transitions = sum(
                a.segment != b.segment for a, b in zip(before_vnfs, before_vnfs[1:])
            )
            after_transitions = sum(
                a.segment != b.segment for a, b in zip(after_vnfs, after_vnfs[1:])
            )
            if after_transitions > before_transitions:
                return False
        return True

    # ---------- Eq. (8) candidate actions and action simulation ----------
    def _affected_vnfs(self, state):
        ranked = []
        for v in state.vnfs.values():
            peak = max(v.demand[r] / max(v.allocated[r], .01) for r in RESOURCES)
            node_u = max(self.resource_utilization(state, v.node_id).values())
            severity = max(peak - .82, .36 - peak, node_u - .87)
            if severity > 0:
                ranked.append((severity, v.id))
        return [vid for _, vid in sorted(ranked, reverse=True)[:8]]

    def _decision_peak(self, v):
        raw = max(v.demand[r] / max(v.allocated[r], .01) for r in RESOURCES)
        return self.decision_load_ema.get(v.id, raw)

    def _action_cooldown(self, action):
        return {
            "scale_up": 20, "scale_down": 30, "scale_out": 40,
            "scale_in": 45, "migration": 60,
            "instance_consolidation": 60,
            "infrastructure_consolidation": 90,
        }.get(action, 30)

    def _eligible_after_cooldown(self, state, action, v):
        last = self.last_action_time.get(v.id, -10**9)
        return state.time - last >= self._action_cooldown(action)

    def _candidate_targets(self, state, action):
        """Select targets using the operational condition of each action."""
        vnfs = list(state.vnfs.values())
        peak = self._decision_peak
        if action in ("scale_up", "scale_out"):
            ranked = sorted((v for v in vnfs if peak(v) >= .82), key=peak, reverse=True)
        elif action == "scale_down":
            ranked = sorted((v for v in vnfs if peak(v) < .48), key=peak)
        elif action == "scale_in":
            ranked = [v for v in vnfs if any(x.id != v.id and x.slice_id == v.slice_id and
                      x.profile == v.profile for x in vnfs)]
            ranked.sort(key=peak)
        elif action == "instance_consolidation":
            counts = {}
            for v in vnfs:
                if not v.consolidated_with:
                    counts[v.profile] = counts.get(v.profile, 0) + 1
            ranked = [v for v in vnfs if not v.consolidated_with and counts.get(v.profile, 0) > 1]
            ranked.sort(key=lambda v: sum(v.demand.values()), reverse=True)
        elif action == "infrastructure_consolidation":
            low_nodes = sorted((n for n in state.nodes.values() if n.active and n.segment not in ("RAN", "TRANSPORT")
                                and 0 < self.node_utilization(state, n.id) < .40),
                               key=lambda n: self.node_utilization(state, n.id))
            ranked = []
            for node in low_nodes:
                resident = next((v for v in vnfs if v.node_id == node.id), None)
                if resident:
                    ranked.append(resident)
        elif action == "migration":
            # Include VNFs on inefficient/lightly loaded nodes as well as
            # overloaded functions, so migration can prepare node release.
            ranked = sorted(vnfs, key=lambda v: (self.node_utilization(state, v.node_id), -peak(v)))
        else:
            ranked = [state.vnfs[x] for x in self._affected_vnfs(state) if x in state.vnfs]
        return [v.id for v in ranked if self._eligible_after_cooldown(state, action, v)][:self.max_candidates_per_action]

    def _persistent_targets(self, state, action):
        current = self._candidate_targets(state, action)
        current_keys = {(action, vid) for vid in current}
        for key in list(self.condition_persistence):
            if key[0] == action and key not in current_keys:
                self.condition_persistence.pop(key, None)
        eligible = []
        for vid in current:
            key = (action, vid)
            self.condition_persistence[key] = self.condition_persistence.get(key, 0) + 1
            if self.condition_persistence[key] >= self.persistence_windows:
                eligible.append(vid)
        return eligible

    def _reserve_feasible(self, state, node_ids):
        limit = 1.0 - self.consolidation_reserve
        for nid in node_ids:
            node = state.nodes[nid]
            if node.segment in ("EDGE", "CORE"):
                if max(self.resource_utilization(state, nid).values()) > limit + 1e-9:
                    return False
        return True

    def _candidate(self, state, action, vid):
        trial = copy.deepcopy(state)
        v = trial.vnfs.get(vid)
        if not v: return None
        target_segment = v.segment
        segment_before = self.segment_power(state).get(target_segment,0.0)
        before = self.total_power(state)
        migration = reconfig = switching = 0.0
        reason = ""
        affected_ids = (v.id,)
        old_active = sum(n.active for n in trial.nodes.values())

        if action == "scale_up":
            if not v.scalable_vertical: return None
            proposed = {r: max(v.allocated[r], v.demand[r] * 1.18) for r in RESOURCES}
            if not self._fits(trial, v, trial.nodes[v.node_id], proposed, exclude=v.id): return None
            v.allocated = proposed; reconfig = .08; reason = "demand close to or above allocated capacity"
        elif action == "scale_down":
            if not v.scalable_vertical: return None
            peak = max(v.demand[r] / max(v.allocated[r], .01) for r in RESOURCES)
            if peak >= .48: return None
            v.allocated = {r: max(v.demand[r] * 1.28, v.allocated[r] * .72) for r in RESOURCES}
            reconfig = .04; reason = "over-provisioning"
        elif action == "scale_out":
            if not v.scalable_horizontal: return None
            peak = max(v.demand[r] / max(v.allocated[r], .01) for r in RESOURCES)
            if peak < .88: return None
            clone = v.clone(); clone.id = f"{v.id}-R{sum(x.profile==v.profile for x in trial.vnfs.values())+1}"
            clone.consolidated_with = ""
            clone.instance += 1; clone.node_id = ""; clone.segment = ""
            if not self._place_energy(trial, clone): return None
            trial.vnfs[clone.id] = clone; trial.slices[v.slice_id].vnf_ids.append(clone.id)
            for r in RESOURCES: v.demand[r] *= .5; clone.demand[r] = v.demand[r]
            reconfig = .16; reason = "insufficient capacity and load distribution"
        elif action == "scale_in":
            peers = [x for x in trial.vnfs.values() if x.slice_id == v.slice_id and x.profile == v.profile and x.id != v.id]
            if not peers: return None
            peer = peers[0]
            combined = {r: v.demand[r] + peer.demand[r] for r in RESOURCES}
            if any(combined[r] > peer.allocated[r] * .82 for r in RESOURCES): return None
            for r in RESOURCES: peer.demand[r] = combined[r]
            self._unlink_consolidation(trial, v.id)
            trial.slices[v.slice_id].vnf_ids.remove(v.id); del trial.vnfs[v.id]
            reconfig = .09; reason = "redundant instance with low utilization"
        elif action == "migration":
            if not v.migratable: return None
            old = v.node_id
            candidates = [n for n in trial.nodes.values() if n.id != old and self._fits(trial, v, n, exclude=v.id)]
            if trial.slices[v.slice_id].service == "URLLC":
                candidates = [n for n in candidates if n.segment == v.segment]
            if not candidates: return None
            candidates.sort(key=lambda n: (not n.active, n.max_power_w, -self.node_utilization(trial, n.id)))
            dest = candidates[0]
            source_node = trial.nodes[old]
            migration = self.migration_energy_wh(v,source_node,dest,trial)
            self._unlink_consolidation(trial, v.id)
            v.node_id, v.segment, dest.active = dest.id, dest.segment, True
            reason = "better energy placement or overload relief"
        elif action == "instance_consolidation":
            if v.consolidated_with:
                return None
            # Instance consolidation: two compatible instances of the same
            # function retain their IDs and share execution resources.
            peers = [x for x in trial.vnfs.values() if x.id != v.id and x.active and
                     not x.consolidated_with and x.profile == v.profile and
                     x.segment in v.allowed_segments and v.segment in x.allowed_segments]
            if not peers:
                return None
            peers.sort(key=lambda x: (x.node_id != v.node_id,
                                      x.segment != v.segment,
                                      -self.node_utilization(trial, x.node_id)))
            peer = peers[0]
            source = trial.nodes[v.node_id]
            destination = trial.nodes[peer.node_id]
            if v.node_id != peer.node_id:
                if not v.migratable or not self._fits(trial, v, destination, exclude=v.id):
                    return None
                migration = self.migration_energy_wh(v, source, destination, trial)
                v.node_id, v.segment, destination.active = destination.id, destination.segment, True
            v.consolidated_with = peer.id
            peer.consolidated_with = v.id
            affected_ids = (v.id, peer.id)
            reconfig = .10
            reason = "compatible VNF instances consolidated with a 20% combined-demand reduction"
            if not self._reserve_feasible(trial, {v.node_id}):
                return None
        elif action == "infrastructure_consolidation":
            source = trial.nodes[v.node_id]
            residents = [x for x in trial.vnfs.values() if x.node_id == source.id]
            if source.segment in ("RAN", "TRANSPORT") or self.node_utilization(trial, source.id) >= .40 or not residents:
                return None
            for resident in residents:
                if not resident.migratable:
                    return None
                destinations = [n for n in trial.nodes.values() if n.active and n.id != source.id and
                                n.segment in resident.allowed_segments and
                                self._fits(trial, resident, n, exclude=resident.id)]
                if trial.slices[resident.slice_id].service == "URLLC":
                    destinations = [n for n in destinations if n.segment == resident.segment]
                if not destinations:
                    return None
                destinations.sort(key=lambda n: (-self.node_utilization(trial, n.id), n.max_power_w))
                dest = destinations[0]
                self._unlink_consolidation(trial, resident.id)
                migration += self.migration_energy_wh(resident, source, dest, trial)
                resident.node_id, resident.segment, dest.active = dest.id, dest.segment, True
            affected_ids = tuple(x.id for x in residents)
            destination_ids = {trial.vnfs[x.id].node_id for x in residents}
            if not self._reserve_feasible(trial, destination_ids):
                return None
            source.active = False
            switching = .12
            reason = "underutilized node evacuated and released to sleep mode"
        else:
            return None

        self._refresh_nodes(trial, allow_sleep=True)
        feasible = self.capacity_feasible(trial) and self.qos_preserved(state, trial)
        if action in ("migration", "instance_consolidation", "infrastructure_consolidation"):
            affects_urlcc = any(
                aid in state.vnfs
                and state.vnfs[aid].slice_id in state.slices
                and state.slices[state.vnfs[aid].slice_id].service == "URLLC"
                for aid in affected_ids
            )
            if affects_urlcc:
                feasible = feasible and self.urlcc_resilience_preserved(state, trial)
        after = self.total_power(trial)
        saving = before - after  # Eq. (9)
        segment_after = self.segment_power(trial).get(target_segment,0.0)
        segment_saving_pct = 100*(segment_before-segment_after)/max(segment_before,1)
        if sum(n.active for n in trial.nodes.values()) != old_active: switching += .08
        return Candidate(action, vid, trial, before, after, saving, migration, reconfig, switching,
                         self.qos_penalty(trial), reason=reason, feasible=feasible,
                         segment_saving_pct=segment_saving_pct,
                         consolidation_partner=(peer.id if action == "instance_consolidation" else ""),
                         instance_saving_pct=(100*self.instance_consolidation_saving if action == "instance_consolidation" else 0.0),
                         consolidation_mode=(action if action in ("instance_consolidation", "infrastructure_consolidation") else ""),
                         released_nodes=(1 if action == "infrastructure_consolidation" else 0),
                         affected_vnfs=affected_ids)

    def migration_energy_wh(self, v, source, destination, state):
        """Energy cost from state size, transfer time and participating nodes."""
        transport = next((n for n in state.nodes.values() if n.segment=="TRANSPORT"),None)
        available_mbps = max(
            100.0,
            (transport.capacity["bandwidth"] - self.transport_hop_traffic_mbps(state))
            if transport else 1000.0,
        )
        duration_s = v.state_gb*8*1024/available_mbps
        overhead_w = .18*source.max_power_w + .18*destination.max_power_w
        if transport: overhead_w += .12*transport.max_power_w
        return duration_s*overhead_w/3600

    def optimize(self):
        state = self.mechanism
        actions = ("scale_up", "scale_down", "scale_out", "scale_in", "migration",
                   "instance_consolidation", "infrastructure_consolidation")
        candidates = []
        for action in actions:
            for vid in self._persistent_targets(state, action):
                c = self._candidate(state, action, vid)
                if c and c.feasible: candidates.append(c)
        if not candidates:
            self._record_no_action("no feasible action")
            return

        # Horizon-aware Eq. (10): predicted energy saving must recover the
        # complete adaptation cost before an action is selected.
        current_penalty = self.qos_penalty(state)
        for c in candidates:
            predicted_saving_wh = c.saving * self.decision_horizon_s / 3600
            adaptation_wh = c.migration_cost + c.reconfiguration_cost + c.switching_cost
            qos_gain = current_penalty - c.qos_penalty
            recent_actions = sum(1 for vid in c.affected_vnfs
                                 if state.time - self.last_action_time.get(vid, -10**9) < self.decision_horizon_s)
            churn_penalty = .05 * recent_actions
            c.projected_net_benefit_wh = predicted_saving_wh - adaptation_wh - churn_penalty
            # QoS improvement can justify elasticity even when its immediate
            # energy balance is negative; degradation is never rewarded.
            c.score = c.projected_net_benefit_wh + 5.0 * qos_gain
            self.candidate_log.append({"time": state.time, "action": c.action, "target": c.target,
                "saving_W": c.saving, "saving_pct":100*c.saving/max(c.power_before,1), "segment_saving_pct":c.segment_saving_pct, "migration_energy_Wh": c.migration_cost, "reconfiguration_cost": c.reconfiguration_cost,
                "switching_cost": c.switching_cost, "qos_penalty": c.qos_penalty, "score": c.score, "feasible": c.feasible,
                "consolidation_partner":c.consolidation_partner,"instance_saving_pct":c.instance_saving_pct,
                "consolidation_mode":c.consolidation_mode,"released_nodes":c.released_nodes,
                "projected_horizon_s":self.decision_horizon_s,
                "projected_net_benefit_Wh":c.projected_net_benefit_wh})
        best = max(candidates, key=lambda c: c.score)  # Eq. (14)
        if best.score <= 0:  # Eqs. (15)-(17): a0
            self._record_no_action("the net benefit is not positive")
            return
        self._commit_candidate(best, "AUTO")

    def _commit_candidate(self, best, source):
        old = self.mechanism
        self.mechanism = best.state
        self.mechanism.time = old.time
        self.mechanism.history = old.history
        self.mechanism.actions = old.actions
        self.mechanism.cumulative_energy_wh = old.cumulative_energy_wh
        self.mechanism.migration_energy_wh = old.migration_energy_wh + best.migration_cost
        self.mechanism.accepted = old.accepted
        self.mechanism.rejected = old.rejected
        for vid in best.affected_vnfs:
            self.last_action_time[vid] = old.time
            self.last_action_kind[vid] = best.action
            for action in ("scale_up", "scale_down", "scale_out", "scale_in", "migration",
                           "instance_consolidation", "infrastructure_consolidation"):
                self.condition_persistence.pop((action, vid), None)
        target = self.mechanism.vnfs.get(best.target)
        logged_action = "consolidation" if best.consolidation_mode else best.action
        self.mechanism.actions.append({"time": self.mechanism.time, "scenario": self.mechanism.name,
            "slice": target.slice_id if target else "-", "vnf": best.target, "profile": target.profile if target else "-",
            "action": logged_action, "reason": best.reason, "saving_W": best.saving,
            "saving_pct":100*best.saving/max(best.power_before,1), "segment_saving_pct":best.segment_saving_pct, "migration_energy_Wh":best.migration_cost,
            "adaptation_cost": best.migration_cost + best.reconfiguration_cost + best.switching_cost,
            "qos_penalty": best.qos_penalty, "score": best.score, "source":source,
            "consolidation_partner":best.consolidation_partner,"instance_saving_pct":best.instance_saving_pct,
            "consolidation_mode":best.consolidation_mode,"released_nodes":best.released_nodes,
            "projected_horizon_s":self.decision_horizon_s,
            "projected_net_benefit_Wh":best.projected_net_benefit_wh})

    def force_action(self, action, vnf_id):
        """Administrator-requested action; mathematical feasibility remains mandatory."""
        candidate = self._candidate(self.mechanism, action, vnf_id)
        if not candidate or not candidate.feasible:
            self.event_log.append({"time":self.baseline.time,"event":"ADMIN_REJECTED","target":vnf_id,
                                   "detail":f"{action}: infeasible due to capacity, compatibility, or QoS"})
            return False
        current_penalty = self.qos_penalty(self.mechanism)
        candidate.score = (candidate.saving / max(abs(candidate.power_before),1) -
                           (candidate.migration_cost+candidate.reconfiguration_cost+candidate.switching_cost) -
                           (candidate.qos_penalty-current_penalty))
        self._commit_candidate(candidate, "ADMIN")
        self.event_log.append({"time":self.baseline.time,"event":"ADMIN_ACTION","target":vnf_id,
                               "detail":f"{action} applied"})
        return True

    def _record_no_action(self, reason):
        self.mechanism.actions.append({"time": self.mechanism.time, "scenario": self.mechanism.name, "slice":"-", "vnf":"-", "profile":"-",
            "action":"no_action", "reason":reason, "saving_W":0, "adaptation_cost":0, "qos_penalty":self.qos_penalty(self.mechanism), "score":0,
            "consolidation_partner":"","instance_saving_pct":0.0,
            "consolidation_mode":"","released_nodes":0,
            "projected_horizon_s":self.decision_horizon_s,
            "projected_net_benefit_Wh":0.0})

    # ---------- arrivals and manual events ----------
    def add_slice_request(self, service, users=None, request_kind="external request"):
        sid = self._new_slice_id(); low, high = SERVICE_USERS[service]
        users = int(users or self.rng.integers(low, high + 1))
        templates = []
        ns_template = NetworkSlice(sid, service, dict(SLICE_QOS[service]), users/SERVICE_REFERENCE[service], users, users)
        for profile in SLICE_CHAINS[service]: templates.append(self._new_vnf(ns_template, profile))
        for state in (self.baseline, self.mechanism):
            ns = ns_template.clone(); ns.vnf_ids = []; state.slices[sid] = ns
            accepted_all = True
            for template in templates:
                v = template.clone()
                placed = self._place_first_fit(state, v) if state is self.baseline else self._place_energy(state, v)
                if placed: state.vnfs[v.id] = v; ns.vnf_ids.append(v.id); state.accepted += 1
                else: state.rejected += 1; accepted_all = False
            self._update_demands_for_state(state)
        self._register_demand_profile(sid, service, users)
        self.event_log.append({"time":self.baseline.time,"event":"NEW_SLICE","target":sid,
                               "detail":f"{service}, {users} users · {request_kind}"})

    def terminate_slice(self, slice_id, reason="sustained zero demand"):
        if slice_id not in self.baseline.slices:
            return False
        for state in (self.baseline, self.mechanism):
            ns = state.slices.get(slice_id)
            if not ns: continue
            for vid in list(ns.vnf_ids):
                self._unlink_consolidation(state, vid)
                state.vnfs.pop(vid, None)
                self.manual_overrides.pop(vid, None)
            del state.slices[slice_id]
            self._refresh_nodes(state, allow_sleep=(state is self.mechanism and state.time >= self.optimization_start))
        self.slice_demand_profiles.pop(slice_id, None)
        self.event_log.append({"time":self.baseline.time,"event":"SLICE_TERMINATED","target":slice_id,"detail":reason})
        return True

    def _update_slice_lifecycle(self):
        """Deactivate and terminate slices from observed inactivity, never age."""
        terminate = []
        for sid, ns in list(self.baseline.slices.items()):
            if ns.users == 0:
                ns.zero_demand_steps += 1
            else:
                ns.zero_demand_steps = 0
            status = "INACTIVE" if ns.zero_demand_steps >= self.slice_inactive_after_s else "ACTIVE"
            for state in (self.baseline, self.mechanism):
                if sid in state.slices:
                    state.slices[sid].zero_demand_steps = ns.zero_demand_steps
                    state.slices[sid].status = status
            if status == "INACTIVE" and ns.zero_demand_steps == self.slice_inactive_after_s:
                self.event_log.append({"time":self.baseline.time,"event":"SLICE_INACTIVE","target":sid,
                                       "detail":f"zero demand persisted for {self.slice_inactive_after_s} s"})
            if ns.zero_demand_steps >= self.slice_terminate_after_s:
                terminate.append(sid)
        for sid in terminate:
            self.terminate_slice(sid, f"zero demand persisted for {self.slice_terminate_after_s} s")

    def _automatic_slice_request(self):
        """Create an external service request and its complete initial VNF chain."""
        if self.workload_mode == "steady" and len(self.baseline.slices) >= int(self.target_active_slices):
            self.next_slice_request_time = self._sample_next_slice_request(self.baseline.time)
            return
        services = list(SLICE_CHAINS)
        pressure = {}
        for service in services:
            matching = [ns.demand_factor for ns in self.baseline.slices.values()
                        if ns.service == service and ns.status == "ACTIVE"]
            pressure[service] = float(np.mean(matching)) if matching else 1.0
        weights = np.array([max(.15, pressure[s]) for s in services], dtype=float)
        weights /= weights.sum()
        service = str(self.rng.choice(services, p=weights))
        request_kind = "replacement after sustained inactivity" if self.workload_mode == "steady" else "external request"
        self.add_slice_request(service, request_kind=request_kind)
        self.next_slice_request_time = self._sample_next_slice_request(self.baseline.time)

    def set_slice_users(self, slice_id, users):
        if slice_id in self.slice_demand_profiles:
            self.slice_demand_profiles[slice_id]["current_users"] = int(users)
            self.slice_demand_profiles[slice_id]["target_users"] = int(users)
        for state in (self.baseline, self.mechanism):
            if slice_id in state.slices:
                state.slices[slice_id].users = int(users)
                state.slices[slice_id].target_users = int(users)
                state.slices[slice_id].demand_factor = users / SERVICE_REFERENCE[state.slices[slice_id].service]
                self._update_demands_for_state(state)
        self.event_log.append({"time":self.baseline.time,"event":"USER_CHANGE","target":slice_id,"detail":f"{users} users"})

    def modify_vnf(self, vnf_id, cpu_pct, ram_pct, bandwidth_pct):
        source = self.baseline.vnfs.get(vnf_id)
        if not source: return
        absolute = {"cpu":source.allocated["cpu"]*cpu_pct/100,"ram":source.allocated["ram"]*ram_pct/100,
                    "bandwidth":source.allocated["bandwidth"]*bandwidth_pct/100}
        for state in (self.baseline, self.mechanism):
            if vnf_id in state.vnfs: state.vnfs[vnf_id].demand.update(absolute)
        self.event_log.append({"time":self.baseline.time,"event":"VNF_LOAD","target":vnf_id,"detail":f"CPU {cpu_pct}%, RAM {ram_pct}%, BW {bandwidth_pct}%"})

    def configure_vnf(self, vnf_id, allocated, demand_pct, duration=30):
        """Edit one VNF identically in both twins and hold a load profile."""
        for state in (self.baseline, self.mechanism):
            if vnf_id in state.vnfs:
                v = state.vnfs[vnf_id]
                v.allocated.update({r: float(allocated[r]) for r in RESOURCES})
        self.manual_overrides[vnf_id] = {
            "percent": {r: float(demand_pct[r]) for r in RESOURCES},
            "until": self.baseline.time + int(duration),
        }
        self._update_demands_for_state(self.baseline)
        self._update_demands_for_state(self.mechanism)
        detail = ", ".join(f"{r.upper()} {demand_pct[r]:.0f}%" for r in RESOURCES)
        self.event_log.append({"time":self.baseline.time,"event":"VNF_PROFILE","target":vnf_id,
                               "detail":f"{detail} for {duration}s"})

    # ---------- simulation clock ----------
    def step(self, steps=1, automatic_arrivals=True):
        for _ in range(steps):
            self.baseline.time += 1; self.mechanism.time += 1
            self._update_users()
            self._update_slice_lifecycle()
            self._update_demands_for_state(self.baseline); self._update_demands_for_state(self.mechanism)
            live_ids = set(self.mechanism.vnfs)
            for vid in list(self.decision_load_ema):
                if vid not in live_ids:
                    self.decision_load_ema.pop(vid, None)
            for v in self.mechanism.vnfs.values():
                raw_peak = max(v.demand[r] / max(v.allocated[r], .01) for r in RESOURCES)
                previous = self.decision_load_ema.get(v.id, raw_peak)
                self.decision_load_ema[v.id] = (self.demand_ema_alpha * raw_peak +
                                                 (1-self.demand_ema_alpha) * previous)
            if self.baseline.time % 5 == 0 and self.baseline.slices:
                busiest = max(self.baseline.slices.values(), key=lambda s: s.users)
                self.event_log.append({"time":self.baseline.time,"event":"DEMAND_UPDATE","target":busiest.id,
                    "detail":f"{busiest.users} users · factor {busiest.demand_factor:.2f}x"})
            if automatic_arrivals and self.baseline.time >= self.next_slice_request_time:
                self._automatic_slice_request()
            self._record_workload()
            if self.mechanism.time == self.optimization_start:
                self.event_log.append({"time":self.baseline.time,"event":"MECHANISM_ENABLED","target":"SYSTEM",
                                       "detail":"optimization starts after the common warm-up period"})
            if self.mechanism.time >= self.optimization_start and self.mechanism.time % self.decision_interval == 0:
                self.optimize()
            self._refresh_nodes(self.baseline, allow_sleep=False)
            self._refresh_nodes(self.mechanism, allow_sleep=self.mechanism.time >= self.optimization_start)
            self._record_metrics(self.baseline); self._record_metrics(self.mechanism)

    def _record_workload(self):
        """Record the shared exogenous workload once per simulated instant."""
        profiles = [p for sid, p in self.slice_demand_profiles.items()
                    if sid in self.baseline.slices or sid in self.mechanism.slices]
        regimes = {name: sum(p["regime"] == name for p in profiles)
                   for name in DEMAND_REGIME_MULTIPLIERS}
        self.workload_history.append({
            "time": self.baseline.time,
            "offered_users": sum(int(p["current_users"]) for p in profiles),
            "target_users": sum(int(p["target_users"]) for p in profiles),
            "active_slices": len(profiles),
            "workload_mode": self.workload_mode,
            **{f"{name.lower()}_regime_slices": count for name, count in regimes.items()},
        })

    def _refresh_nodes(self, state, allow_sleep):
        occupied = {v.node_id for v in state.vnfs.values() if v.active}
        ran_nodes = sorted([n for n in state.nodes.values() if n.segment=="RAN"],key=lambda n:n.id)
        required_ran = min(len(ran_nodes),max(1,int(np.ceil(self.radio_load_units(state)/max(ran_nodes[0].capacity["bandwidth"],1))))) if state.slices and ran_nodes else 0
        ran_required_ids = {n.id for n in ran_nodes if n.id in occupied}
        for n in ran_nodes:
            if len(ran_required_ids)>=required_ran: break
            ran_required_ids.add(n.id)
        for n in state.nodes.values():
            if n.segment == "TRANSPORT":
                n.active = self.transport_hop_traffic_mbps(state) > 0
                continue
            if n.segment == "RAN":
                n.active = bool(state.slices) if not allow_sleep else n.id in ran_required_ids
                continue
            if n.id in occupied: n.active = True
            elif allow_sleep: n.active = False  # Eq. (18): empty node can sleep
            else: n.active = True

    def _record_metrics(self, state):
        power = self.segment_power(state); energy = sum(power.values()) / 3600
        state.cumulative_energy_wh += energy
        violations = 0
        for ns in state.slices.values():
            q = self.slice_qos(state, ns)
            minimum = ns.qos["throughput_mbps"] * np.clip(ns.demand_factor, .2, 2.4)
            ok = q["latency_ms"] <= ns.qos["latency_ms"] and q["jitter_ms"] <= ns.qos["jitter_ms"] and q["loss_pct"] <= ns.qos["loss_pct"] and q["throughput_mbps"] >= minimum
            violations += not ok
            self.qos_history.append({"time":state.time,"scenario":state.name,"slice":ns.id,"service":ns.service,"users":ns.users,
                **q,"latency_limit":ns.qos["latency_ms"],"jitter_limit":ns.qos["jitter_ms"],"loss_limit":ns.qos["loss_pct"],
                "throughput_min":minimum,"compliant":bool(ok)})
        qrows = [self.slice_qos(state, ns) for ns in state.slices.values()]
        offered_users = sum(int(p["current_users"]) for sid, p in self.slice_demand_profiles.items()
                            if sid in self.baseline.slices or sid in self.mechanism.slices)
        row = {"time":state.time,"scenario":state.name,"power_total_w":sum(power.values()),
               "energy_cumulative_wh":state.cumulative_energy_wh + state.migration_energy_wh,
               "active_nodes":sum(n.active for n in state.nodes.values()),"qos_violations":violations,
               "accepted_vnfs":len(state.vnfs),"rejected":state.rejected,
               "offered_users":offered_users,
               "served_users":sum(s.users for s in state.slices.values()),
               "users":sum(s.users for s in state.slices.values()),
               "active_slices":len(state.slices),
               "latency_avg_ms":np.mean([q["latency_ms"] for q in qrows]) if qrows else 0,
               "jitter_avg_ms":np.mean([q["jitter_ms"] for q in qrows]) if qrows else 0,
               "loss_avg_pct":np.mean([q["loss_pct"] for q in qrows]) if qrows else 0,
               "throughput_avg_mbps":np.mean([q["throughput_mbps"] for q in qrows]) if qrows else 0}
        active_nodes = [n for n in state.nodes.values() if n.active]
        for r in RESOURCES:
            row[f"{r}_util_avg_pct"] = 100 * np.mean([
                self.resource_utilization(state, n.id)[r] for n in active_nodes
            ]) if active_nodes else 0
        # Segment-specific operational indicators. RAN radio resources are
        # intentionally separated from virtual CPU/RAM used by vDU/vCU.
        radio_units = self.radio_load_units(state)
        row["ran_prb_util_pct"] = min(100.0, 100 * radio_units / 14000)
        transport_nodes = [n for n in state.nodes.values() if n.segment=="TRANSPORT" and n.active]
        edge_nodes = [n for n in state.nodes.values() if n.segment=="EDGE" and n.active]
        core_nodes = [n for n in state.nodes.values() if n.segment=="CORE" and n.active]
        transport = self.transport_route_metrics(state)
        row["transport_traffic_mbps"] = transport["traffic_mbps"]
        row["transport_hop_mbps"] = transport["hop_mbps"]
        row["transport_avg_hops"] = transport["average_hops"]
        row["active_transport_links"] = transport["active_links"]
        row["transport_bw_util_pct"] = 100*transport["hop_mbps"]/sum(n.capacity["bandwidth"] for n in transport_nodes) if transport_nodes else 0
        row["edge_cpu_util_pct"] = 100*np.mean([self.resource_utilization(state,n.id)["cpu"] for n in edge_nodes]) if edge_nodes else 0
        row["core_cpu_util_pct"] = 100*np.mean([self.resource_utilization(state,n.id)["cpu"] for n in core_nodes]) if core_nodes else 0
        row.update({f"power_{s.lower()}_w":v for s,v in power.items()}); state.history.append(row)

    # ---------- data views ----------
    def history_df(self): return pd.DataFrame(self.baseline.history + self.mechanism.history)
    def actions_df(self): return pd.DataFrame(self.baseline.actions + self.mechanism.actions)
    def candidates_df(self): return pd.DataFrame(self.candidate_log)
    def qos_df(self): return pd.DataFrame(self.qos_history)
    def workload_df(self): return pd.DataFrame(self.workload_history)
    def slice_energy_df(self, scenario):
        """Attribute instantaneous E2E power to slices by weighted demand share."""
        state = self.baseline if scenario == "Heuristic" else self.mechanism
        rows = {sid:{"Slice":sid,"Service":ns.service,"Users":ns.users,"RAN":0.0,"EDGE":0.0,"TRANSPORT":0.0,"CORE":0.0}
                for sid,ns in state.slices.items()}
        for node in state.nodes.values():
            if node.segment == "TRANSPORT":
                continue
            residents = [v for v in state.vnfs.values() if v.node_id == node.id]
            shares = {}
            for v in residents:
                effective = self._effective_demand(state, v)
                shares[v.slice_id] = shares.get(v.slice_id,0) + sum(SEGMENT_WEIGHTS[node.segment][r]*effective[r]/max(node.capacity[r],1) for r in RESOURCES)
            total = sum(shares.values())
            if total > 0:
                for sid,share in shares.items(): rows[sid][node.segment] += self.node_power(state,node)*share/total
        # Attribute idle/unassigned segment power proportionally so that the
        # sum of slice estimates equals measured infrastructure power.
        for segment in ("RAN","EDGE","CORE"):
            measured = sum(self.node_power(state,n) for n in state.nodes.values() if n.segment==segment)
            attributed = sum(row[segment] for row in rows.values())
            residual = max(0, measured-attributed)
            weights = {sid:sum(sum(self._effective_demand(state,v).values()) for v in state.vnfs.values() if v.slice_id==sid and v.segment==segment) for sid in rows}
            total_weight = sum(weights.values()) or sum(row["Users"] for row in rows.values()) or 1
            for sid,row in rows.items():
                weight = weights[sid] if sum(weights.values()) else row["Users"]
                row[segment] += residual*weight/total_weight
        # Transport power is attributed by placement-aware hop traffic.
        transport_power = sum(self.node_power(state,n) for n in state.nodes.values() if n.segment=="TRANSPORT")
        traffic = {
            sid:self.slice_transport_metrics(state, state.slices[sid])["hop_mbps"]
            for sid in rows
        }
        total_traffic = sum(traffic.values()) or 1
        for sid in rows: rows[sid]["TRANSPORT"] += transport_power*traffic[sid]/total_traffic
        for row in rows.values(): row["Total E2E"] = row["RAN"]+row["EDGE"]+row["TRANSPORT"]+row["CORE"]
        return pd.DataFrame(rows.values())

    def vnf_power_share(self, scenario, vnf_id):
        state = self.baseline if scenario == "Heuristic" else self.mechanism
        v = state.vnfs.get(vnf_id)
        if not v: return 0.0
        node = state.nodes[v.node_id]
        peers = [x for x in state.vnfs.values() if x.node_id==node.id]
        if node.segment in ("RAN","TRANSPORT"):
            # Radio/transport power is slice-level; return the VNF's fair share
            # only for display, without treating CPU/storage as physical RAN load.
            weights={x.id:max(.001,self._effective_demand(state,x)["bandwidth"]) for x in peers}
        else:
            weights={x.id:sum(SEGMENT_WEIGHTS[node.segment][r]*self._effective_demand(state,x)[r]/max(node.capacity[r],1) for r in RESOURCES) for x in peers}
        return self.node_power(state,node)*weights.get(v.id,0)/max(sum(weights.values()),1e-9)
    def vnf_df(self, scenario):
        state = self.baseline if scenario == "Heuristic" else self.mechanism
        rows=[]
        for v in state.vnfs.values():
            ns=state.slices[v.slice_id]
            rows.append({"VNF":v.id,"Slice":v.slice_id,"Service":ns.service,"Slice status":ns.status,
                "Slice users":ns.users,"Type":v.profile,
                "Segment":v.segment,"Node":v.node_id,"Consolidated with":v.consolidated_with or "—","CPU %":100*v.demand["cpu"]/max(v.allocated["cpu"],.01),
                "RAM %":100*v.demand["ram"]/max(v.allocated["ram"],.01),"Storage %":100*v.demand["storage"]/max(v.allocated["storage"],.01),
                "BW %":100*v.demand["bandwidth"]/max(v.allocated["bandwidth"],.01)})
        return pd.DataFrame(rows)
