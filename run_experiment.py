import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from simulator import TwinSimulation


CSV_NAMES = {
    "history": "history.csv",
    "actions": "actions.csv",
    "candidates": "candidates.csv",
    "qos": "qos.csv",
    "events": "events.csv",
    "workload": "workload.csv",
}

CSV_SCHEMAS = {
    "history": [
        "time", "scenario", "power_total_w", "energy_cumulative_wh",
        "active_nodes", "qos_violations", "accepted_vnfs", "rejected",
        "offered_users", "served_users", "users", "active_slices",
        "latency_avg_ms", "jitter_avg_ms", "loss_avg_pct",
        "throughput_avg_mbps", "cpu_util_avg_pct", "ram_util_avg_pct",
        "storage_util_avg_pct", "bandwidth_util_avg_pct", "ran_prb_util_pct",
        "transport_traffic_mbps", "transport_hop_mbps",
        "transport_avg_hops", "active_transport_links",
        "transport_bw_util_pct", "edge_cpu_util_pct", "core_cpu_util_pct",
        "power_ran_w", "power_edge_w", "power_transport_w", "power_core_w",
    ],
    "actions": [
        "time", "scenario", "slice", "vnf", "profile", "action", "reason",
        "saving_W", "adaptation_cost", "qos_penalty", "score",
        "consolidation_partner", "instance_saving_pct", "consolidation_mode",
        "released_nodes", "projected_horizon_s",
        "projected_net_benefit_Wh", "saving_pct", "segment_saving_pct",
        "migration_energy_Wh", "source",
    ],
    "candidates": [
        "time", "action", "target", "saving_W", "saving_pct",
        "segment_saving_pct", "migration_energy_Wh", "reconfiguration_cost",
        "switching_cost", "qos_penalty", "score", "feasible",
        "consolidation_partner", "instance_saving_pct", "consolidation_mode",
        "released_nodes", "projected_horizon_s", "projected_net_benefit_Wh",
    ],
    "qos": [
        "time", "scenario", "slice", "service", "users", "latency_ms",
        "jitter_ms", "loss_pct", "throughput_mbps", "latency_limit",
        "jitter_limit", "loss_limit", "throughput_min", "compliant",
    ],
    "events": ["time", "event", "target", "detail"],
    "workload": [
        "time", "offered_users", "target_users", "active_slices",
        "workload_mode", "low_regime_slices", "normal_regime_slices",
        "high_regime_slices", "burst_regime_slices",
    ],
}


def append_frame(frame, path, schema):
    if frame.empty:
        return
    frame = frame.reindex(columns=schema)
    frame.to_csv(path, mode="a", header=not path.exists(), index=False)


def flush_logs(sim, output):
    """Persist one checkpoint and release rows already written to disk."""
    append_frame(sim.history_df(), output / CSV_NAMES["history"], CSV_SCHEMAS["history"])
    append_frame(sim.actions_df(), output / CSV_NAMES["actions"], CSV_SCHEMAS["actions"])
    append_frame(sim.candidates_df(), output / CSV_NAMES["candidates"], CSV_SCHEMAS["candidates"])
    append_frame(sim.qos_df(), output / CSV_NAMES["qos"], CSV_SCHEMAS["qos"])
    append_frame(pd.DataFrame(sim.event_log), output / CSV_NAMES["events"], CSV_SCHEMAS["events"])
    append_frame(sim.workload_df(), output / CSV_NAMES["workload"], CSV_SCHEMAS["workload"])

    sim.baseline.history.clear()
    sim.mechanism.history.clear()
    sim.baseline.actions.clear()
    sim.mechanism.actions.clear()
    sim.candidate_log.clear()
    sim.qos_history.clear()
    sim.event_log.clear()
    sim.workload_history.clear()


def write_status(output, config, sim, complete=False):
    payload = {
        "version": "7.4",
        "complete": bool(complete),
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "simulated_time": int(sim.baseline.time),
        "configured_steps": int(config["steps"]),
        "seed": int(config["seed"]),
        "workload_mode": config["workload_mode"],
        "initial_vnfs": int(config["vnfs"]),
        "target_active_slices": int(sim.target_active_slices),
    }
    temporary = output / "status.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(output / "status.json")


def main():
    parser = argparse.ArgumentParser(
        description="Run the NS energy experiment without rendering the dashboard"
    )
    parser.add_argument("--steps", type=int, default=3600)
    parser.add_argument("--seed", type=int, default=20260720)
    parser.add_argument("--vnfs", type=int, default=153)
    parser.add_argument("--slice-request-interval", type=int, default=120)
    parser.add_argument("--slice-inactivity", type=int, default=120)
    parser.add_argument("--demand-regime-interval", type=int, default=90)
    parser.add_argument("--demand-volatility", type=float, default=0.75)
    parser.add_argument(
        "--workload-mode",
        choices=("steady", "growth"),
        default="steady",
        help="steady replaces inactive slices; growth permits net slice arrivals",
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=50,
        help="simulated seconds between durable CSV writes",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=0.02,
        help="wall-clock rest per simulated second; increase if the computer gets warm",
    )
    parser.add_argument("--output", default="results/run_01")
    args = parser.parse_args()

    if args.steps < 1:
        parser.error("--steps must be at least 1")
    if args.checkpoint_interval < 1:
        parser.error("--checkpoint-interval must be at least 1")
    if args.pace < 0:
        parser.error("--pace cannot be negative")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    existing = [output / name for name in CSV_NAMES.values() if (output / name).exists()]
    if existing:
        parser.error(
            f"output directory already contains experiment CSV files: {output}. "
            "Choose a new --output directory to preserve reproducibility."
        )

    config = vars(args).copy()
    (output / "configuration.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    sim = TwinSimulation(
        seed=args.seed,
        initial_vnfs=args.vnfs,
        mean_slice_request_interval_s=args.slice_request_interval,
        slice_inactive_after_s=max(10, args.slice_inactivity // 2),
        slice_terminate_after_s=args.slice_inactivity,
        demand_regime_mean_s=args.demand_regime_interval,
        demand_volatility=args.demand_volatility,
        workload_mode=args.workload_mode,
    )
    write_status(output, config, sim)

    remaining = args.steps
    started = time.monotonic()
    try:
        while remaining:
            batch = min(args.checkpoint_interval, remaining)
            sim.step(batch)
            flush_logs(sim, output)
            remaining -= batch
            write_status(output, config, sim)
            print(
                f"\rSimulated {sim.baseline.time:,}/{args.steps:,} s "
                f"({100 * sim.baseline.time / args.steps:5.1f}%)",
                end="",
                flush=True,
            )
            if args.pace:
                time.sleep(args.pace * batch)
    except KeyboardInterrupt:
        flush_logs(sim, output)
        write_status(output, config, sim)
        print(f"\nInterrupted safely at simulated second {sim.baseline.time}.")
        print(f"Partial results preserved in: {output.resolve()}")
        return

    sim.vnf_df("Heuristic").to_csv(output / "vnfs_heuristic_final.csv", index=False)
    sim.vnf_df("Proposed mechanism").to_csv(
        output / "vnfs_mechanism_final.csv", index=False
    )
    write_status(output, config, sim, complete=True)
    elapsed = time.monotonic() - started
    print(
        f"\nExperiment complete: {args.steps:,} simulated seconds, "
        f"{args.vnfs} initial VNFs, seed {args.seed}."
    )
    print(f"Wall-clock execution: {elapsed:.1f} s")
    print(f"Results: {output.resolve()}")


if __name__ == "__main__":
    main()
