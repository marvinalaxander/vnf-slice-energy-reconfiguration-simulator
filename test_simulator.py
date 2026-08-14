from catalog import SLICE_CHAINS
from simulator import TwinSimulation


def test_twins_begin_identically():
    sim = TwinSimulation(seed=7, initial_vnfs=30)
    assert set(sim.baseline.vnfs) == set(sim.mechanism.vnfs)
    for vid in sim.baseline.vnfs:
        assert sim.baseline.vnfs[vid].node_id == sim.mechanism.vnfs[vid].node_id


def test_dynamic_run_records_both_scenarios():
    sim = TwinSimulation(seed=8, initial_vnfs=30)
    sim.step(5, automatic_arrivals=False)
    history = sim.history_df()
    assert len(history) == 10
    assert set(history.scenario) == {"Heuristic", "Proposed mechanism"}


def test_manual_load_is_shared():
    sim = TwinSimulation(seed=9, initial_vnfs=30)
    vid = sorted(sim.baseline.vnfs)[0]
    sim.modify_vnf(vid, 125, 90, 110)
    assert sim.baseline.vnfs[vid].demand["cpu"] == sim.mechanism.vnfs[vid].demand["cpu"]


def test_instance_consolidation_preserves_both_ids_and_reduces_pair_demand():
    sim = TwinSimulation(seed=11, initial_vnfs=60)
    state = sim.mechanism
    candidate = None
    for vid in sorted(state.vnfs):
        candidate = sim._candidate(state, "instance_consolidation", vid)
        if candidate and candidate.feasible:
            break
    assert candidate is not None
    v = candidate.state.vnfs[vid]
    peer = candidate.state.vnfs[v.consolidated_with]
    assert v.id in candidate.state.vnfs and peer.id in candidate.state.vnfs
    assert peer.consolidated_with == v.id
    raw = v.demand["cpu"] + peer.demand["cpu"]
    effective = (sim._effective_demand(candidate.state, v)["cpu"] +
                 sim._effective_demand(candidate.state, peer)["cpu"])
    assert abs(effective - raw * .80) < 1e-9


def test_unlinking_one_instance_keeps_the_other_vnf():
    sim = TwinSimulation(seed=12, initial_vnfs=60)
    candidate = next((sim._candidate(sim.mechanism, "instance_consolidation", vid)
                      for vid in sorted(sim.mechanism.vnfs)
                      if sim._candidate(sim.mechanism, "instance_consolidation", vid)), None)
    assert candidate is not None
    v = candidate.state.vnfs[candidate.target]
    peer_id = v.consolidated_with
    sim._unlink_consolidation(candidate.state, v.id)
    assert candidate.state.vnfs[peer_id].consolidated_with == ""
    assert v.id in candidate.state.vnfs and peer_id in candidate.state.vnfs


def test_infrastructure_consolidation_releases_a_node():
    sim = TwinSimulation(seed=13, initial_vnfs=90)
    candidate = None
    for vid in sim._candidate_targets(sim.mechanism, "infrastructure_consolidation"):
        candidate = sim._candidate(sim.mechanism, "infrastructure_consolidation", vid)
        if candidate and candidate.feasible:
            break
    assert candidate is not None
    assert candidate.consolidation_mode == "infrastructure_consolidation"
    assert candidate.released_nodes == 1
    assert sum(not n.active for n in candidate.state.nodes.values()) >= 1


def test_slice_has_no_predetermined_expiration():
    sim = TwinSimulation(seed=14, initial_vnfs=30)
    assert not hasattr(sim, "slice_end_times")
    initial_ids = set(sim.baseline.slices)
    sim.step(20, automatic_arrivals=False)
    assert initial_ids.issubset(sim.baseline.slices)


def test_initial_population_contains_only_complete_slice_chains():
    sim = TwinSimulation(seed=141, initial_vnfs=153)
    assert len(sim.baseline.vnfs) == 153
    for ns in sim.baseline.slices.values():
        profiles = [sim.baseline.vnfs[vid].profile for vid in ns.vnf_ids]
        assert all(profile in profiles for profile in SLICE_CHAINS[ns.service])


def test_zero_demand_terminates_after_persistent_inactivity():
    sim = TwinSimulation(seed=15, initial_vnfs=30,
                         slice_inactive_after_s=2, slice_terminate_after_s=4)
    sid = sorted(sim.baseline.slices)[0]
    sim.set_slice_users(sid, 0)
    sim.step(3, automatic_arrivals=False)
    assert sid in sim.baseline.slices
    assert sim.baseline.slices[sid].status == "INACTIVE"
    sim.step(1, automatic_arrivals=False)
    assert sid not in sim.baseline.slices
    assert sid not in sim.mechanism.slices


def test_automatic_arrival_creates_slice_chain_not_independent_vnf_event():
    sim = TwinSimulation(seed=16, initial_vnfs=30, mean_slice_request_interval_s=1,
                         workload_mode="growth")
    sim.next_slice_request_time = 1
    sim.step(1, automatic_arrivals=True)
    arrivals = [e for e in sim.event_log if e["event"] == "NEW_SLICE"]
    assert arrivals
    sid = arrivals[-1]["target"]
    assert len(sim.baseline.slices[sid].vnf_ids) == len(SLICE_CHAINS[sim.baseline.slices[sid].service])
    assert not any(e["event"] == "NEW_VNF" for e in sim.event_log)


def test_stochastic_demand_is_identical_in_both_twins():
    sim = TwinSimulation(seed=17, initial_vnfs=30, demand_regime_mean_s=10)
    sim.step(80, automatic_arrivals=False)
    assert any(e["event"] == "DEMAND_REGIME" for e in sim.event_log)
    shared = set(sim.baseline.slices).intersection(sim.mechanism.slices)
    assert shared
    for sid in shared:
        assert sim.baseline.slices[sid].users == sim.mechanism.slices[sid].users
        assert sim.baseline.slices[sid].target_users == sim.mechanism.slices[sid].target_users


def test_shared_offered_workload_is_logged_for_both_scenarios():
    sim = TwinSimulation(seed=171, initial_vnfs=30, demand_regime_mean_s=10)
    sim.step(80, automatic_arrivals=True)
    history = sim.history_df().pivot(
        index="time", columns="scenario", values="offered_users"
    )
    assert (history["Heuristic"] == history["Proposed mechanism"]).all()
    assert len(sim.workload_df()) == 80


def test_steady_mode_does_not_grow_slice_population():
    sim = TwinSimulation(seed=172, initial_vnfs=30, mean_slice_request_interval_s=1,
                         workload_mode="steady")
    initial_count = len(sim.baseline.slices)
    sim.step(30, automatic_arrivals=True)
    assert len(sim.baseline.slices) <= initial_count
    assert len(sim.mechanism.slices) <= initial_count


def test_same_seed_reproduces_stochastic_workload():
    first = TwinSimulation(seed=18, initial_vnfs=30, demand_regime_mean_s=15)
    second = TwinSimulation(seed=18, initial_vnfs=30, demand_regime_mean_s=15)
    first.step(100, automatic_arrivals=True)
    second.step(100, automatic_arrivals=True)
    first_events = [(e["time"], e["event"], e["target"], e["detail"]) for e in first.event_log]
    second_events = [(e["time"], e["event"], e["target"], e["detail"]) for e in second.event_log]
    assert first_events == second_events


def test_zero_user_dashboard_value_is_valid():
    sim = TwinSimulation(seed=19, initial_vnfs=30)
    sid = sorted(sim.baseline.slices)[0]
    sim.set_slice_users(sid, 0)
    assert sim.baseline.slices[sid].users == 0


def test_urlcc_migration_keeps_the_same_segment():
    sim = TwinSimulation(seed=20, initial_vnfs=90)
    for v in sim.mechanism.vnfs.values():
        if sim.mechanism.slices[v.slice_id].service != "URLLC":
            continue
        candidate = sim._candidate(sim.mechanism, "migration", v.id)
        if candidate and candidate.feasible:
            assert candidate.state.vnfs[v.id].segment == v.segment


def test_transport_offered_traffic_is_shared_but_routes_are_scenario_specific():
    sim = TwinSimulation(seed=21, initial_vnfs=30)
    assert sim.transport_traffic_mbps(sim.baseline) == sim.transport_traffic_mbps(sim.mechanism)
    assert sim.transport_hop_traffic_mbps(sim.baseline) == sim.transport_hop_traffic_mbps(sim.mechanism)

    ns = next(ns for ns in sim.mechanism.slices.values() if ns.service == "eMBB")
    for vid in ns.vnf_ids:
        v = sim.mechanism.vnfs[vid]
        if v.profile in ("vDU", "vCU"):
            v.segment = "RAN"
        elif v.profile in ("UPF", "Cache", "Firewall"):
            v.segment = "EDGE"

    assert sim.transport_traffic_mbps(sim.baseline) == sim.transport_traffic_mbps(sim.mechanism)
    assert sim.transport_hop_traffic_mbps(sim.baseline) != sim.transport_hop_traffic_mbps(sim.mechanism)


def test_transport_hops_follow_vnf_chain_placement():
    sim = TwinSimulation(seed=22, initial_vnfs=30)
    ns = next(ns for ns in sim.mechanism.slices.values() if ns.service == "eMBB")

    for vid in ns.vnf_ids:
        v = sim.mechanism.vnfs[vid]
        v.segment = "EDGE" if v.profile in ("vDU", "vCU") else "CORE"
    two_hop_route = sim.slice_transport_metrics(sim.mechanism, ns)

    for vid in ns.vnf_ids:
        v = sim.mechanism.vnfs[vid]
        v.segment = "RAN" if v.profile in ("vDU", "vCU") else "EDGE"
    one_hop_route = sim.slice_transport_metrics(sim.mechanism, ns)

    assert abs(two_hop_route["average_hops"] - 2.0) < 1e-9
    assert abs(one_hop_route["average_hops"] - 1.0) < 1e-9
    assert abs(two_hop_route["hop_mbps"] - 2 * one_hop_route["hop_mbps"]) < 1e-9


def test_history_preserves_old_fields_and_adds_route_aware_transport_fields():
    sim = TwinSimulation(seed=23, initial_vnfs=30)
    sim.step(1, automatic_arrivals=False)
    history = sim.history_df()
    old_fields = {
        "transport_bw_util_pct", "power_transport_w", "power_total_w",
        "power_ran_w", "power_edge_w", "power_core_w",
    }
    new_fields = {
        "transport_traffic_mbps", "transport_hop_mbps",
        "transport_avg_hops", "active_transport_links",
    }
    assert old_fields.issubset(history.columns)
    assert new_fields.issubset(history.columns)


def test_zero_user_slice_adds_no_transport_traffic():
    sim = TwinSimulation(seed=24, initial_vnfs=30)
    sid = sorted(sim.baseline.slices)[0]
    before = sim.slice_transport_metrics(sim.baseline, sim.baseline.slices[sid])
    sim.set_slice_users(sid, 0)
    after = sim.slice_transport_metrics(sim.baseline, sim.baseline.slices[sid])
    assert before["traffic_mbps"] > 0
    assert after["traffic_mbps"] == 0
    assert after["hop_mbps"] == 0
