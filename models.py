from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Tuple


RESOURCES = ("cpu", "ram", "storage", "bandwidth")
SEGMENTS = ("RAN", "EDGE", "TRANSPORT", "CORE")


@dataclass
class VNFProfile:
    name: str
    allowed_segments: Tuple[str, ...]
    cpu_range: Tuple[float, float]
    ram_range: Tuple[float, float]
    storage_range: Tuple[float, float]
    bandwidth_range: Tuple[float, float]
    latency_ms: float
    state_gb_range: Tuple[float, float]
    migratable: bool = True
    scalable_vertical: bool = True
    scalable_horizontal: bool = True


@dataclass
class VNF:
    id: str
    slice_id: str
    profile: str
    allowed_segments: Tuple[str, ...]
    segment: str
    node_id: str
    allocated: Dict[str, float]
    demand: Dict[str, float]
    latency_limit_ms: float
    state_gb: float
    priority: int
    migratable: bool
    scalable_vertical: bool
    scalable_horizontal: bool
    instance: int = 1
    active: bool = True
    # Reciprocal link to another compatible VNF instance. Both identifiers are
    # preserved; consolidation only reduces their combined effective demand.
    consolidated_with: str = ""

    def clone(self) -> "VNF":
        return replace(self, allocated=dict(self.allocated), demand=dict(self.demand))


@dataclass
class NetworkSlice:
    id: str
    service: str
    qos: Dict[str, float]
    demand_factor: float = 1.0
    users: int = 0
    target_users: int = 0
    vnf_ids: List[str] = field(default_factory=list)
    status: str = "ACTIVE"
    zero_demand_steps: int = 0

    def clone(self) -> "NetworkSlice":
        return replace(self, qos=dict(self.qos), vnf_ids=list(self.vnf_ids))


@dataclass
class Node:
    id: str
    segment: str
    capacity: Dict[str, float]
    idle_power_w: float
    max_power_w: float
    sleep_power_w: float = 8.0
    active: bool = True

    def clone(self) -> "Node":
        return replace(self, capacity=dict(self.capacity))


@dataclass
class ScenarioState:
    name: str
    nodes: Dict[str, Node]
    slices: Dict[str, NetworkSlice]
    vnfs: Dict[str, VNF]
    time: int = 0
    cumulative_energy_wh: float = 0.0
    migration_energy_wh: float = 0.0
    rejected: int = 0
    accepted: int = 0
    history: List[dict] = field(default_factory=list)
    actions: List[dict] = field(default_factory=list)

    def clone(self, name: str) -> "ScenarioState":
        return ScenarioState(
            name=name,
            nodes={k: v.clone() for k, v in self.nodes.items()},
            slices={k: v.clone() for k, v in self.slices.items()},
            vnfs={k: v.clone() for k, v in self.vnfs.items()},
        )
