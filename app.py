import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from catalog import SLICE_CHAINS, VNF_PROFILES
from simulator import TwinSimulation


st.set_page_config(page_title="NS Energy Twin Lab v7.4", page_icon="⚡", layout="wide")
st.markdown("""
<style>
.block-container{padding-top:1rem;max-width:1550px}
[data-testid="stMetric"]{background:color-mix(in srgb, var(--background-color) 82%, #3467eb 18%);border:1px solid #50627a;border-radius:12px;padding:12px}
[data-testid="stMetricValue"],[data-testid="stMetricLabel"]{color:inherit!important}
.event{padding:.55rem .8rem;border-left:4px solid #4f8cff;background:rgba(79,140,255,.13);color:inherit;margin:.25rem 0;border-radius:5px}
.live-dot{display:inline-block;width:11px;height:11px;border-radius:50%;background:#19c37d;box-shadow:0 0 10px #19c37d;margin-right:7px}
.paused-dot{display:inline-block;width:11px;height:11px;border-radius:50%;background:#f5a623;margin-right:7px}
</style>""", unsafe_allow_html=True)

if "sim" not in st.session_state:
    st.session_state.sim = TwinSimulation()
if "running" not in st.session_state:
    st.session_state.running = True
sim = st.session_state.sim

if st.session_state.running:
    st_autorefresh(interval=1000, key="live_clock")
    sim.step(1, True)

st.title("⚡ NS Energy Twin Lab v7.4 · Route-Aware Transport")
st.caption("Shared stationary E2E workload · unchanged decision mechanism · heuristic vs. optimization")

# Main transport controls remain visible even when the sidebar is collapsed.
ctl1,ctl2,ctl3,ctl4,ctl5=st.columns([1,1,1,1.4,4.6])
if ctl1.button("▶ Start",width="stretch",type="primary"):
    st.session_state.running=True; st.rerun()
if ctl2.button("⏸ Pause",width="stretch"):
    st.session_state.running=False; st.rerun()
if ctl3.button("⏭ +1 s",width="stretch"):
    sim.step(1,True); st.rerun()
if ctl4.button("⏩ +10 s",width="stretch"):
    sim.step(10,True); st.rerun()
status_class="live-dot" if st.session_state.running else "paused-dot"
status_text="LIVE MONITORING" if st.session_state.running else "PAUSED"
ctl5.markdown(f"<div style='padding:.55rem'><span class='{status_class}'></span><b>{status_text}</b> · clock t={sim.baseline.time}s · 1 s refresh</div>",unsafe_allow_html=True)

with st.sidebar:
    st.header("Configuration")
    st.subheader("New configuration")
    number=st.number_input("Initial VNFs",min_value=15,max_value=3000,value=153,step=1)
    seed=st.number_input("Seed",min_value=1,value=20260720,step=1)
    interval=st.slider("Decision interval (s)",1,60,int(sim.decision_interval))
    warmup=st.slider("Common warm-up before optimization (s)",0,180,int(sim.optimization_start))
    sim.optimization_start=warmup
    request_interval=st.slider("Mean interval between new slice requests (s)",30,600,int(sim.mean_slice_request_interval_s),10)
    regime_interval=st.slider("Mean demand-regime duration (s)",30,300,int(sim.demand_regime_mean_s),10)
    volatility=st.slider("Demand volatility",0.5,1.5,float(sim.demand_volatility),0.1)
    workload_mode=st.selectbox(
        "Slice-population mode",
        ["steady", "growth"],
        index=0 if sim.workload_mode=="steady" else 1,
        help="Steady replaces slices terminated after sustained inactivity; growth permits net new arrivals.",
    )
    inactivity=st.slider("Slice inactivity before termination (s)",30,600,int(sim.slice_terminate_after_s),10)
    sim.mean_slice_request_interval_s=int(request_interval)
    sim.demand_regime_mean_s=int(regime_interval)
    sim.demand_volatility=float(volatility)
    sim.workload_mode=workload_mode
    sim.slice_inactive_after_s=max(10,int(inactivity)//2)
    sim.slice_terminate_after_s=int(inactivity)
    if st.button("Recreate experiment",width="stretch"):
        st.session_state.running=True
        st.session_state.sim=TwinSimulation(
            seed=int(seed),
            initial_vnfs=int(number),
            decision_interval=interval,
            optimization_start=int(warmup),
            mean_slice_request_interval_s=int(request_interval),
            slice_inactive_after_s=max(10, int(inactivity)//2),
            slice_terminate_after_s=int(inactivity),
            demand_regime_mean_s=int(regime_interval),
            demand_volatility=float(volatility),
            workload_mode=workload_mode,
        )
        st.rerun()
    st.divider()
    st.subheader("Manual events")
    event_type=st.selectbox("Event",["Change users","Add slice","Terminate slice","Request administrative action"])
    if event_type=="Change users":
        slice_ids=sorted(sim.baseline.slices)
        if not slice_ids:
            st.info("No active slices are currently available.")
        else:
            sid=st.selectbox("Slice",slice_ids,key="user_sid")
            current=max(0,int(sim.baseline.slices[sid].users))
            users=st.number_input("Users",min_value=0,max_value=100000,value=current,step=max(1,current//20))
            if st.button("Apply demand",width="stretch"): sim.set_slice_users(sid,users); sim.step(1,False); st.rerun()
    elif event_type=="Add slice":
        service=st.selectbox("Service",list(SLICE_CHAINS))
        users=st.number_input("Initial users",min_value=1,max_value=100000,value=500)
        if st.button("Submit request",width="stretch"): sim.add_slice_request(service,users); sim.step(1,False); st.rerun()
    elif event_type=="Terminate slice":
        slice_ids=sorted(sim.baseline.slices)
        if not slice_ids:
            st.info("No active slices are currently available.")
        else:
            sid=st.selectbox("Slice to terminate",slice_ids,key="end_sid")
            if st.button("Terminate in both scenarios",width="stretch"): sim.terminate_slice(sid,"administrative event"); st.rerun()
    else:
        action=st.selectbox("Action",["scale_up","scale_down","scale_out","scale_in","migration",
                                      "instance_consolidation","infrastructure_consolidation"])
        vnf_ids=sorted(sim.mechanism.vnfs)
        if not vnf_ids:
            st.info("No VNF instances are currently available.")
        else:
            vid=st.selectbox("Target VNF",vnf_ids,key="admin_vid")
            st.caption("The request is rejected if it violates capacity, compatibility, or QoS/SLA.")
            if st.button("Request from mechanism",width="stretch"): sim.force_action(action,vid); st.rerun()

if not sim.baseline.history:
    sim.step(1,False)
hist=sim.history_df()
latest=hist.sort_values("time").groupby("scenario").tail(1).set_index("scenario")
b=latest.loc["Heuristic"]; m=latest.loc["Proposed mechanism"]
saving=(b.energy_cumulative_wh-m.energy_cumulative_wh)/max(b.energy_cumulative_wh,.001)*100

cols=st.columns(6)
def delta(column, scenario="Heuristic"):
    rows=hist[hist.scenario==scenario].sort_values("time")
    return rows.iloc[-1][column]-rows.iloc[-2][column] if len(rows)>1 else 0
cols[0].metric("Shared offered users",f"{int(b.get('offered_users',b.users)):,}",
               f"{int(delta('offered_users' if 'offered_users' in hist.columns else 'users')):+,} /s")
cols[1].metric("Slices",len(sim.baseline.slices))
cols[2].metric("Heuristic power",f"{b.power_total_w:,.0f} W",f"{delta('power_total_w'):+.1f} W/s")
cols[3].metric("Mechanism power",f"{m.power_total_w:,.0f} W",f"{delta('power_total_w','Proposed mechanism'):+.1f} W/s",delta_color="inverse")
cols[4].metric("Cumulative savings",f"{saving:.2f}%",f"{b.energy_cumulative_wh-m.energy_cumulative_wh:+.2f} Wh")
cols[5].metric("QoS violations H / M",f"{int(b.qos_violations)} / {int(m.qos_violations)}")

tabs=st.tabs(["Control center","Demand and VNFs","E2E energy","QoS/SLA","Infrastructure","Decisions","Data and formulas"])

with tabs[0]:
    left,right=st.columns([1.35,.65])
    with left:
        fig=px.line(hist,x="time",y="power_total_w",color="scenario",labels={"time":"Time (s)","power_total_w":"Power (W)","scenario":"Scenario"},
                    color_discrete_map={"Heuristic":"#ef8354","Proposed mechanism":"#3467eb"})
        fig.update_layout(template="plotly_white",title="Real-time E2E power",hovermode="x unified",height=390)
        if sim.optimization_start>0:
            fig.add_vline(x=sim.optimization_start,line_dash="dash",line_color="#19c37d",annotation_text="Mechanism activation")
        st.plotly_chart(fig,width="stretch",key="control_power_e2e")
    with right:
        st.subheader("Recent events")
        if not sim.event_log: st.info("No arrivals or manual changes have been recorded yet.")
        for e in sim.event_log[-7:][::-1]:
            st.markdown(f"<div class='event'><b>t={e['time']} · {e['event']}</b><br>{e['target']} · {e['detail']}</div>",unsafe_allow_html=True)
    actions=sim.actions_df()
    if not actions.empty:
        last=actions.tail(1).iloc[0]
        st.success(f"Latest decision: {last['action']} on {last['vnf']} — {last['reason']} · score={last['score']:.3f}")
    mechanism_live=hist[hist.scenario=="Proposed mechanism"].sort_values("time").tail(120)
    st.subheader("Segment-specific indicators")
    segment_cols={"ran_prb_util_pct":"RAN · radio/PRB load","transport_bw_util_pct":"Transport · hop-weighted traffic","edge_cpu_util_pct":"Edge · CPU","core_cpu_util_pct":"Core · CPU"}
    seg_live=mechanism_live.melt(id_vars="time",value_vars=list(segment_cols),var_name="indicator",value_name="utilization")
    seg_live["indicator"]=seg_live.indicator.map(segment_cols)
    fig_seg=px.line(seg_live,x="time",y="utilization",color="indicator",labels={"time":"Time (s)","utilization":"Utilization (%)","indicator":"Indicator"})
    fig_seg.update_layout(template="plotly_white",height=360,hovermode="x unified",title="Operational resources by segment — mechanism")
    st.plotly_chart(fig_seg,width="stretch",key="control_segment_resources")

with tabs[1]:
    users_df=pd.DataFrame([{"Slice":s.id,"Service":s.service,"Users":s.users,"Demand factor":s.demand_factor,
                            "Baseline VNFs":len(s.vnf_ids)} for s in sim.baseline.slices.values()])
    fig=px.scatter(users_df,x="Slice",y="Users",color="Service",size="Demand factor",title="Users and demand by network slice")
    fig.update_layout(template="plotly_white",height=380)
    st.plotly_chart(fig,width="stretch",key="demand_users_slices")
    st.subheader("Isolate and compare a network slice")
    selected_slice=st.selectbox("Slice to analyze",sorted(sim.baseline.slices),key="analysis_slice")
    ec1,ec2=st.columns(2)
    for scenario,box,color in [("Heuristic",ec1,"#ef8354"),("Proposed mechanism",ec2,"#3467eb")]:
        state=sim.baseline if scenario=="Heuristic" else sim.mechanism
        sdf=sim.slice_energy_df(scenario)
        erow=sdf[sdf.Slice==selected_slice]
        box.markdown(f"### {scenario}")
        if erow.empty:
            box.warning("Slice unavailable")
            continue
        erow=erow.iloc[0]
        box.metric("Power attributed to slice",f"{erow['Total E2E']:.2f} W")
        parts=pd.DataFrame({"Segment":["RAN","EDGE","TRANSPORT","CORE"],"Power":[erow.RAN,erow.EDGE,erow.TRANSPORT,erow.CORE]})
        pie=px.bar(parts,x="Segment",y="Power",color="Segment",title="Attributed E2E consumption")
        pie.update_layout(template="plotly_white",height=310,showlegend=False)
        box.plotly_chart(pie,width="stretch",key=f"slice_energy_{scenario}_{selected_slice}")
        ids=state.slices[selected_slice].vnf_ids if selected_slice in state.slices else []
        dist=pd.DataFrame([{"VNF":v.id,"Function":v.profile,"Segment":v.segment,"Node":v.node_id,
                            "Consolidated with":v.consolidated_with or "—"} for v in state.vnfs.values() if v.id in ids])
        box.dataframe(dist,width="stretch",hide_index=True,height=230)
    st.subheader("Individual VNF comparison")
    common_ids=sorted(set(sim.baseline.vnfs)&set(sim.mechanism.vnfs))
    chosen=st.selectbox("Inspect VNF",common_ids,key="inspect_vnf")
    compare_cols=st.columns(2)
    for scenario,box in [("Heuristic",compare_cols[0]),("Proposed mechanism",compare_cols[1])]:
        state_side=sim.baseline if scenario=="Heuristic" else sim.mechanism
        vdf_side=sim.vnf_df(scenario); d=vdf_side[vdf_side.VNF==chosen].iloc[0]
        box.markdown(f"### {scenario}")
        box.write(f"**{d['Type']} · {d['Slice']} · {d['Segment']} / {d['Node']}**")
        mc1,mc2=box.columns(2)
        mc1.metric("Slice users",f"{int(d['Slice users']):,}")
        mc2.metric("Attributed power",f"{sim.vnf_power_share(scenario,chosen):.3f} W")
        node=state_side.nodes[d['Node']]
        box.metric("Host-node power",f"{sim.node_power(state_side,node):.2f} W")
        box.caption(f"Consolidated with: {d['Consolidated with']}")
        recent=sim.actions_df()
        recent=recent[recent.vnf==chosen] if not recent.empty else recent
        box.caption("Latest action: "+(str(recent.iloc[-1].action) if not recent.empty else "no reconfiguration"))
    scenario="Heuristic"
    vdf=sim.vnf_df(scenario)
    detail=vdf[vdf.VNF==chosen].iloc[0]
    st.subheader("Define the selected VNF experimental load")
    st.caption("Intensity changes the function demand internally without presenting CPU, storage, or RAM as physical RAN resources.")
    state_view=sim.baseline if scenario=="Heuristic" else sim.mechanism
    selected=state_view.vnfs[chosen]
    intensity=st.slider("Function demand intensity (%)",5,150,75)
    alloc=dict(selected.allocated)
    pct={r:intensity for r in ("cpu","ram","storage","bandwidth")}
    duration=st.slider("Keep this profile for (simulated seconds)",1,300,30)
    if st.button("Apply the same profile to both scenarios",type="primary"):
        sim.configure_vnf(chosen,alloc,pct,duration); sim.step(1,False); st.rerun()

with tabs[2]:
    fig=px.line(hist,x="time",y="energy_cumulative_wh",color="scenario",labels={"time":"Time (s)","energy_cumulative_wh":"Cumulative energy (Wh)","scenario":"Scenario"},
                color_discrete_map={"Heuristic":"#ef8354","Proposed mechanism":"#3467eb"})
    fig.update_layout(template="plotly_white",title="Cumulative energy: experimental comparison",hovermode="x unified")
    st.plotly_chart(fig,width="stretch",key="energy_cumulative_comparison")
    segcols=["power_ran_w","power_edge_w","power_transport_w","power_core_w"]
    now=hist.sort_values("time").groupby("scenario").tail(1)
    long=now.melt(id_vars="scenario",value_vars=segcols,var_name="segment",value_name="power")
    long["segment"]=long.segment.str.replace("power_","",regex=False).str.replace("_w","",regex=False).str.upper()
    fig=px.bar(long,x="segment",y="power",color="scenario",barmode="group",labels={"segment":"Segment","power":"Power (W)","scenario":"Scenario"},
               color_discrete_map={"Heuristic":"#ef8354","Proposed mechanism":"#3467eb"},title="Current power by E2E segment")
    fig.update_layout(template="plotly_white")
    st.plotly_chart(fig,width="stretch",key="energy_segment_comparison")
    c1,c2=st.columns(2)
    for state,title,box in [(sim.baseline,"Heuristic",c1),(sim.mechanism,"Proposed mechanism",c2)]:
        seg=sim.segment_power(state); labels=[title];parents=[""];values=[sum(seg.values())]
        for s,val in seg.items():
            labels.append(s);parents.append(title);values.append(val)
            for n in state.nodes.values():
                if n.segment==s:
                    labels.append(n.id);parents.append(s);values.append(sim.node_power(state,n))
        sun=go.Figure(go.Sunburst(labels=labels,parents=parents,values=values,branchvalues="total"))
        sun.update_layout(title=f"E2E breakdown — {title}",height=500,margin=dict(l=0,r=0,b=0,t=45))
        box.plotly_chart(sun,width="stretch",key=f"energy_sunburst_{title}")

with tabs[3]:
    qdf=sim.qos_df()
    scenario=st.radio("QoS scenario",["Heuristic","Proposed mechanism"],horizontal=True,key="qscenario")
    slice_id=st.selectbox("Slice",sorted(qdf[qdf.scenario==scenario].slice.unique()),key="qos_slice")
    qs=qdf[(qdf.scenario==scenario)&(qdf.slice==slice_id)].sort_values("time")
    specs=[("latency_ms","latency_limit","Latency (ms)",False),("jitter_ms","jitter_limit","Jitter (ms)",False),
           ("loss_pct","loss_limit","Packet loss (%)",False),("throughput_mbps","throughput_min","Throughput (Mbps)",True)]
    for metric,limit,label,_ in specs:
        fig=go.Figure()
        fig.add_trace(go.Scatter(x=qs.time,y=qs[metric],name="Observed",line=dict(color="#3467eb")))
        fig.add_trace(go.Scatter(x=qs.time,y=qs[limit],name="SLA limit",line=dict(color="#e63946",dash="dash")))
        fig.update_layout(template="plotly_white",title=f"{label} — {slice_id}",xaxis_title="Time (s)",yaxis_title=label,height=310,hovermode="x unified")
        st.plotly_chart(fig,width="stretch",key=f"qos_{scenario}_{slice_id}_{metric}")
    current=qdf.sort_values("time").groupby(["scenario","slice"]).tail(1).copy()
    current["Status"]=current.compliant.map({True:"Compliant",False:"VIOLATION"})
    st.dataframe(current[["scenario","slice","service","users","latency_ms","latency_limit","jitter_ms","jitter_limit","loss_pct","loss_limit","throughput_mbps","throughput_min","Status"]],width="stretch",hide_index=True)

with tabs[4]:
    st.subheader("Infrastructure by segment: simultaneous comparison")
    rows=[]
    for scenario,state in [("Heuristic",sim.baseline),("Proposed mechanism",sim.mechanism)]:
        latest_row=latest.loc[scenario]
        for segment in ("RAN","EDGE","TRANSPORT","CORE"):
            nodes=[n for n in state.nodes.values() if n.segment==segment]
            if segment=="RAN": load=latest_row.ran_prb_util_pct; indicator="Radio/PRB load"
            elif segment=="TRANSPORT": load=latest_row.transport_bw_util_pct; indicator="Traffic × logical hops"
            elif segment=="EDGE": load=latest_row.edge_cpu_util_pct; indicator="Computing load"
            else: load=latest_row.core_cpu_util_pct; indicator="Computing load"
            rows.append({"Scenario":scenario,"Segment":segment,"Indicator":indicator,"Utilization %":load,
                         "Power W":sum(sim.node_power(state,n) for n in nodes),"Active nodes":sum(n.active for n in nodes),
                         "Functions":sum(v.segment==segment for v in state.vnfs.values())})
    ndf=pd.DataFrame(rows)
    c1,c2=st.columns(2)
    f1=px.bar(ndf,x="Segment",y="Power W",color="Scenario",barmode="group",title="Energy consumption by segment",
              color_discrete_map={"Heuristic":"#ef8354","Proposed mechanism":"#3467eb"})
    f1.update_layout(template="plotly_white")
    c1.plotly_chart(f1,width="stretch",key="infra_energy_compare")
    f2=px.bar(ndf,x="Segment",y="Utilization %",color="Scenario",barmode="group",title="Segment-specific load",
              color_discrete_map={"Heuristic":"#ef8354","Proposed mechanism":"#3467eb"})
    f2.update_layout(template="plotly_white")
    c2.plotly_chart(f2,width="stretch",key="infra_load_compare")
    st.dataframe(ndf,width="stretch",hide_index=True)
    st.subheader("Active and energy-saving equipment")
    nc1,nc2=st.columns(2)
    for scenario,state,box in [("Heuristic",sim.baseline,nc1),("Proposed mechanism",sim.mechanism,nc2)]:
        node_rows=[{"Node":n.id,"Segment":n.segment,"State":"ACTIVE" if n.active else "SLEEP",
                    "Power W":sim.node_power(state,n)} for n in state.nodes.values()]
        node_df=pd.DataFrame(node_rows)
        node_fig=px.bar(node_df,x="Node",y="Power W",color="Segment",pattern_shape="State",
                        title=f"{scenario}: power and state by equipment",
                        category_orders={"State":["ACTIVE","SLEEP"]})
        node_fig.update_layout(template="plotly_white",height=430,xaxis_tickangle=-55)
        box.plotly_chart(node_fig,width="stretch",key=f"infra_node_power_{scenario}")
        active=int((node_df.State=="ACTIVE").sum()); sleeping=int((node_df.State=="SLEEP").sum())
        box.caption(f"{active} active devices · {sleeping} devices in energy-saving mode")

with tabs[5]:
    st.info("The mechanism provides six top-level adaptive actions: scale-up, scale-down, scale-out, scale-in, migration, and consolidation. Consolidation has two internal modes: VNF instance consolidation and infrastructure consolidation. The seventh decision is no-action (maintain).")
    actions=sim.actions_df()
    if actions.empty: st.info("The mechanism has not reached a decision instant yet.")
    else:
        display_actions=actions.copy()
        display_actions["display_action"]=display_actions.apply(
            lambda r: r.get("consolidation_mode", "") if r.get("action") == "consolidation" and r.get("consolidation_mode", "") else r.get("action"),axis=1)
        counts=display_actions.groupby("display_action").size().reset_index(name="count")
        fig=px.bar(counts,x="display_action",y="count",color="display_action",title="Selected actions, including both consolidation modes and the maintain decision")
        fig.update_layout(template="plotly_white",showlegend=False)
        st.plotly_chart(fig,width="stretch",key="decisions_action_counts")
        st.dataframe(actions.sort_values("time",ascending=False),width="stretch",hide_index=True)
        mig=actions[actions.action.isin(["migration","consolidation"])]
        if not mig.empty:
            m1,m2,m3=st.columns(3)
            m1.metric("Migrations/consolidations",len(mig))
            m2.metric("Cumulative migration energy",f"{mig.get('migration_energy_Wh',pd.Series(dtype=float)).fillna(0).sum():.4f} Wh")
            cons=mig[mig.action=="consolidation"]
            instance_cons=cons[cons.get("consolidation_mode",pd.Series(index=cons.index,dtype=str))=="instance_consolidation"]
            infra_cons=cons[cons.get("consolidation_mode",pd.Series(index=cons.index,dtype=str))=="infrastructure_consolidation"]
            m3.metric("Infrastructure node-release events",f"{int(infra_cons.get('released_nodes',pd.Series(dtype=float)).fillna(0).sum())}")
            st.caption(f"Instance consolidation events: {len(instance_cons)} · infrastructure consolidation events: {len(infra_cons)} · configured pair-demand reduction: 20%. E2E savings remain measured after migration and infrastructure costs.")
    cand=sim.candidates_df()
    if not cand.empty:
        st.subheader("Candidate-action evaluation")
        st.dataframe(cand.sort_values(["time","score"],ascending=[False,False]).head(200),width="stretch",hide_index=True)

with tabs[6]:
    st.subheader("Implemented article equations")
    st.markdown(r"""
1. $L_n^r(t)=\sum_{v\in V_n(t)}d_v^r(t)$  
2. $L_n^r(t)\leq C_n^r$  
3. $U_n^r(t)=L_n^r(t)/C_n^r$  
4. $U_n(t)=\sum_{r\in R}\alpha_{s(n),r}U_n^r(t)$, $\sum_r\alpha_r=1$  
5–7. $P_n(t)=P_n^{idle}+(P_n^{max}-P_n^{idle})U_n(t)$; sleep: $P_n(t)=P_n^{sleep}$; $P(t)=\sum_nP_n(t)$  
8. $A=\{a_{su},a_{sd},a_{so},a_{si},a_{mig},a_{con},a_0\}$  
9–10. $\Delta P(a,t)=P(t)-P^a(t+1)$; $C_{adapt}=C_{mig}+C_{rec}+C_{sw}$  
11–12. Penalties for latency, jitter, packet loss, and throughput.  
13–17. $S_H(a,t)=\Delta P(a,t)H/3600-E_{adapt}-C_{churn}+\lambda_Q\Delta QoS$; conditions are smoothed and must persist for three decision windows, then the positive maximum or $a_0$ is selected.  
18. A node enters sleep mode only when $V_n(t+1)=\varnothing$.
""")
    st.subheader("Auxiliary functions used in the experiment")
    st.caption("These expressions do not add actions to set A; they calculate migration costs and VNF-instance consolidation results.")
    st.markdown(r"""
**Estimated migration duration**

$$
T_{mig}=\frac{8\times1024\times S_{GB}}{B_{available,Mbps}}
$$

**Energy introduced by migration**

$$
E_{mig}=\frac{T_{mig}}{3600}\left(0.18P^{max}_{src}+0.18P^{max}_{dst}+0.12P^{max}_{transport}\right)
$$

**Placement-aware Transport load**

$$
L_{tr}(t)=\sum_s B_s(t)h_s(t)
$$

$$
P_{tr}(t)=P_{tr}^{idle}+
\left(P_{tr}^{max}-P_{tr}^{idle}\right)
\min\left(1,\frac{L_{tr}(t)}{C_{tr}}\right)
$$

**VNF instance consolidation**

For compatible instances $v_i$ and $v_j$ of the same function, both identifiers are retained and their effective combined demand is:

$$
D^{con}_{i,j}(t)=(1-\eta_{con})\left(D_i(t)+D_j(t)\right),\qquad \eta_{con}=0.20
$$

If either instance is migrated, terminated, or removed, the pair is dissolved and the surviving VNF continues independently.

**Measured E2E saving after consolidation**

$$
\Delta P_{con}=P(t)-P^{con}(t+1)
$$

$$
Saving_{con}(\%)=100\frac{\Delta P_{con}}{P(t)}
$$

**Saving within the affected segment**

$$
Saving_{seg}(\%)=100\frac{P_{seg}(t)-P^{con}_{seg}(t+1)}{P_{seg}(t)}
$$

**Infrastructure consolidation and node release**

For a source node $n$, all hosted VNFs are migrated only when compatible active destinations have sufficient capacity. The node enters sleep mode after it becomes empty:

$$
V_n(t+1)=\varnothing \Rightarrow P_n(t+1)=P_n^{sleep}
$$

$$
B_{infra}=P(t)-P^{infra}(t+1)-\sum_{v\in V_n}E_{mig,v}-C_{rec}-C_{sw}-\pi_{QoS}
$$

**Power attribution to a slice**

$$
P_n^s(t)=P_n(t)\frac{D_n^s(t)}{\sum_{j\in S}D_n^j(t)}
$$

For Transport, $D_n^s(t)=B_s(t)h_s(t)$ is the slice traffic multiplied by the logical hops induced by its VNF-chain placement. For RAN, it is the slice contribution to radio/PRB load. For Edge and Core, it is weighted computing demand.
""")
    st.subheader("Actions and evaluation functions")
    st.dataframe(pd.DataFrame([
        {"Element":"scale-up / scale-down","Type":"Action","Evaluation":"capacity, demand, energy, and QoS"},
        {"Element":"scale-out / scale-in","Type":"Action","Evaluation":"instances, capacity, energy, and QoS"},
        {"Element":"migration","Type":"Action","Evaluation":"E_mig, placement, continuity, and QoS"},
        {"Element":"instance consolidation","Type":"Consolidation mode","Evaluation":"compatible pair, preserved IDs, 20% effective-demand reduction, measured E2E saving, and QoS"},
        {"Element":"infrastructure consolidation","Type":"Consolidation mode","Evaluation":"batch migration, released nodes, sleep power, migration cost, measured E2E saving, and QoS"},
        {"Element":"no-action","Type":"Decision","Evaluation":"selected when the best score is not positive"},
    ]),width="stretch",hide_index=True)
    st.download_button("History CSV",hist.to_csv(index=False).encode(),"history_v7.csv","text/csv")
    st.download_button("Per-slice QoS CSV",sim.qos_df().to_csv(index=False).encode(),"qos_v7.csv","text/csv")
    st.download_button("Actions CSV",sim.actions_df().to_csv(index=False).encode(),"actions_v7.csv","text/csv")
    st.download_button("Candidates CSV",sim.candidates_df().to_csv(index=False).encode(),"candidates_v7.csv","text/csv")
    st.download_button("Lifecycle and demand events CSV",pd.DataFrame(sim.event_log).to_csv(index=False).encode(),
                       "events_v7.csv","text/csv")
