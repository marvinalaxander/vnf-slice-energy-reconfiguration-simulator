import argparse
import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh


def command_line_results():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--results", default="results/run_01")
    args, _ = parser.parse_known_args()
    return args.results


def read_csv_safely(path):
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        # The runner may be replacing the final line at the same instant.
        return pd.DataFrame()


def sample_for_plot(frame, maximum=2500):
    if len(frame) <= maximum:
        return frame
    stride = max(1, len(frame) // maximum)
    sampled = frame.iloc[::stride].copy()
    if sampled.index[-1] != frame.index[-1]:
        sampled = pd.concat([sampled, frame.tail(1)], ignore_index=True)
    return sampled


st.set_page_config(
    page_title="NS Energy Twin Lab v7.4 · Results Monitor",
    page_icon="⚡",
    layout="wide",
)
st.title("⚡ NS Energy Twin Lab v7.4 · Route-Aware Transport Monitor")
st.caption(
    "Read-only visualization of checkpointed results. "
    "The simulation runs independently in the terminal."
)

default_results = command_line_results()
results_text = st.sidebar.text_input("Results directory", value=default_results)
refresh_seconds = st.sidebar.slider("Refresh interval (s)", 3, 30, 5)
results = Path(results_text).expanduser()

status_path = results / "status.json"
status = {}
if status_path.exists():
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        status = {}

if not status.get("complete", False):
    st_autorefresh(interval=refresh_seconds * 1000, key="results_refresh")

history = read_csv_safely(results / "history.csv")
actions = read_csv_safely(results / "actions.csv")
workload = read_csv_safely(results / "workload.csv")

if history.empty:
    st.info(
        "Waiting for the first checkpoint. Start run_experiment.py in another "
        "Visual Studio Code terminal or verify the results directory."
    )
    st.stop()

latest = (
    history.sort_values("time")
    .groupby("scenario", as_index=False)
    .tail(1)
    .set_index("scenario")
)
heuristic = latest.loc["Heuristic"]
mechanism = latest.loc["Proposed mechanism"]
cumulative_saving = (
    100
    * (heuristic.energy_cumulative_wh - mechanism.energy_cumulative_wh)
    / max(heuristic.energy_cumulative_wh, 1e-9)
)

status_label = "COMPLETE" if status.get("complete") else "RUNNING"
st.success(
    f"{status_label} · simulated t={int(history.time.max()):,} s · "
    f"seed={status.get('seed', '—')} · workload={status.get('workload_mode', '—')}"
)

metric_columns = st.columns(6)
metric_columns[0].metric(
    "Shared offered users",
    f"{int(heuristic.get('offered_users', heuristic.users)):,}",
)
metric_columns[1].metric(
    "Active slices", f"{int(heuristic.get('active_slices', 0)):,}"
)
metric_columns[2].metric("Heuristic power", f"{heuristic.power_total_w:,.0f} W")
metric_columns[3].metric("Mechanism power", f"{mechanism.power_total_w:,.0f} W")
metric_columns[4].metric("Cumulative saving", f"{cumulative_saving:.2f}%")
metric_columns[5].metric(
    "Active nodes H / M",
    f"{int(heuristic.active_nodes)} / {int(mechanism.active_nodes)}",
)

tabs = st.tabs(["Energy", "Transport", "Workload", "Decisions", "Experiment status"])

with tabs[0]:
    plotted = sample_for_plot(history.sort_values(["scenario", "time"]))
    figure = px.line(
        plotted,
        x="time",
        y="power_total_w",
        color="scenario",
        labels={
            "time": "Simulated time (s)",
            "power_total_w": "Power (W)",
            "scenario": "Scenario",
        },
        color_discrete_map={
            "Heuristic": "#ef8354",
            "Proposed mechanism": "#3467eb",
        },
        title="E2E power",
    )
    figure.update_layout(template="plotly_white", hovermode="x unified")
    st.plotly_chart(figure, use_container_width=True, key="monitor_power")

    figure = px.line(
        plotted,
        x="time",
        y="energy_cumulative_wh",
        color="scenario",
        labels={
            "time": "Simulated time (s)",
            "energy_cumulative_wh": "Cumulative energy (Wh)",
            "scenario": "Scenario",
        },
        color_discrete_map={
            "Heuristic": "#ef8354",
            "Proposed mechanism": "#3467eb",
        },
        title="Cumulative energy",
    )
    figure.update_layout(template="plotly_white", hovermode="x unified")
    st.plotly_chart(figure, use_container_width=True, key="monitor_energy")

with tabs[1]:
    required = {"power_transport_w", "transport_hop_mbps"}
    if not required.issubset(history.columns):
        st.info("Route-aware Transport metrics are not available yet.")
    else:
        transport_plot = sample_for_plot(
            history.sort_values(["scenario", "time"])
        )
        figure = px.line(
            transport_plot,
            x="time",
            y="power_transport_w",
            color="scenario",
            labels={
                "time": "Simulated time (s)",
                "power_transport_w": "Transport power (W)",
                "scenario": "Scenario",
            },
            color_discrete_map={
                "Heuristic": "#ef8354",
                "Proposed mechanism": "#3467eb",
            },
            title="Placement-aware Transport power",
        )
        figure.update_layout(template="plotly_white", hovermode="x unified")
        st.plotly_chart(
            figure, use_container_width=True, key="monitor_transport_power"
        )

        figure = px.line(
            transport_plot,
            x="time",
            y="transport_hop_mbps",
            color="scenario",
            labels={
                "time": "Simulated time (s)",
                "transport_hop_mbps": "Hop-weighted traffic (Mb/s·hop)",
                "scenario": "Scenario",
            },
            color_discrete_map={
                "Heuristic": "#ef8354",
                "Proposed mechanism": "#3467eb",
            },
            title="Traffic carried across logical Transport hops",
        )
        figure.update_layout(template="plotly_white", hovermode="x unified")
        st.plotly_chart(
            figure, use_container_width=True, key="monitor_transport_hops"
        )

with tabs[2]:
    if workload.empty:
        st.info("The workload checkpoint is not available yet.")
    else:
        workload_plot = sample_for_plot(workload.sort_values("time"))
        figure = px.line(
            workload_plot,
            x="time",
            y=["offered_users", "target_users"],
            labels={
                "time": "Simulated time (s)",
                "value": "Users",
                "variable": "Workload",
            },
            title="Shared exogenous workload",
        )
        figure.update_layout(template="plotly_white", hovermode="x unified")
        st.plotly_chart(figure, use_container_width=True, key="monitor_workload")

        figure = px.line(
            workload_plot,
            x="time",
            y="active_slices",
            labels={
                "time": "Simulated time (s)",
                "active_slices": "Active slices",
            },
            title="Active slice population",
        )
        figure.update_layout(template="plotly_white")
        st.plotly_chart(figure, use_container_width=True, key="monitor_slices")

with tabs[3]:
    if actions.empty:
        st.info("No decision has been recorded yet.")
    else:
        display = actions.copy()
        display["display_action"] = display.apply(
            lambda row: (
                row.get("consolidation_mode", "")
                if row.get("action") == "consolidation"
                and isinstance(row.get("consolidation_mode"), str)
                and row.get("consolidation_mode")
                else row.get("action")
            ),
            axis=1,
        )
        counts = (
            display.groupby("display_action")
            .size()
            .reset_index(name="count")
            .sort_values("count", ascending=False)
        )
        figure = px.bar(
            counts,
            x="display_action",
            y="count",
            color="display_action",
            labels={"display_action": "Decision", "count": "Count"},
            title="Distribution of selected decisions",
        )
        figure.update_layout(template="plotly_white", showlegend=False)
        st.plotly_chart(figure, use_container_width=True, key="monitor_actions")
        st.dataframe(
            actions.sort_values("time", ascending=False).head(200),
            use_container_width=True,
            hide_index=True,
        )

with tabs[4]:
    st.json(status or {"status": "waiting for status.json"})
    configuration_path = results / "configuration.json"
    if configuration_path.exists():
        try:
            st.subheader("Configuration")
            st.json(json.loads(configuration_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            st.warning("The configuration file is being written.")
    st.caption(
        "QoS and candidate files are intentionally not reloaded by this monitor "
        "because they can contain hundreds of thousands of rows. They remain "
        "available in the results directory for final analysis."
    )
