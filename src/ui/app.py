"""
src/ui/app.py — TraceIQ Investigation Canvas (Streamlit UI, Stage 12).
7 sections per 12_FRONTEND_UX_PLAN.md §3.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from datetime import date
import streamlit as st
import plotly.graph_objects as go

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TraceIQ — Investigation Canvas",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .main { background:#0f1117; color:#e8eaf6; }
  .stApp { background:#0f1117; }
  h1,h2,h3 { color:#90caf9; }
  .badge-strong   { background:#1b5e20; color:#a5d6a7; padding:2px 10px; border-radius:12px; font-weight:700; }
  .badge-moderate { background:#e65100; color:#ffccbc; padding:2px 10px; border-radius:12px; font-weight:700; }
  .badge-weak     { background:#4a148c; color:#e1bee7; padding:2px 10px; border-radius:12px; font-weight:700; }
  .badge-insuf    { background:#b71c1c; color:#ffcdd2; padding:2px 10px; border-radius:12px; font-weight:700; }
  .badge-watch    { background:#1565c0; color:#bbdefb; padding:2px 10px; border-radius:12px; font-weight:700; }
  .evidence-card  { background:#1a1f2e; border-left:3px solid #42a5f5; padding:10px 14px; border-radius:6px; margin:6px 0; }
  .hyp-card       { background:#1a1f2e; border-radius:8px; padding:14px; margin:8px 0; border:1px solid #2a3050; }
  .action-card    { background:#0d2137; border:1px solid #1565c0; border-radius:8px; padding:16px; }
  .telemetry-bar  { background:#1a1f2e; border-radius:6px; padding:10px; margin-top:12px; font-size:0.82em; color:#78909c; }
  .guardrail-warn { background:#311b00; border-left:3px solid #ff6f00; padding:8px 12px; border-radius:4px; color:#ffcc02; font-size:0.85em; }
  .demo-label     { background:#263238; color:#80cbc4; font-size:0.75em; padding:2px 8px; border-radius:4px; }
</style>
""", unsafe_allow_html=True)

ANALYSIS_DATE = date(2026, 8, 29)
GRAIN = {"region": "North", "category": "Electronics"}

# ── Helpers ────────────────────────────────────────────────────────────────

CONFIDENCE_BADGE = {
    "Strong":      '<span class="badge-strong">🟢 Strong</span>',
    "Moderate":    '<span class="badge-moderate">🟡 Moderate</span>',
    "Weak":        '<span class="badge-weak">🟠 Weak</span>',
    "Insufficient":'<span class="badge-insuf">🔴 Insufficient</span>',
}

def _badge(label):
    return CONFIDENCE_BADGE.get(label, f"<span>{label}</span>")

@st.cache_data(show_spinner=False)
def _run_pipeline():
    from src.detection.change_detection import run_detection
    from src.decomposition.kpi_decomposition import run_decomposition
    from src.hypothesis.rules import generate_hypotheses
    from src.evidence.structured_evidence import retrieve_evidence
    from src.evidence.retrieval import build_index, retrieve_for_hypothesis
    from src.confidence.scoring import score_all
    from src.ambiguity.gate import evaluate
    from src.recommendation.recommendation_engine import generate_recommendation
    from src.llm.narrative import generate_narrative

    alert   = run_detection("Revenue", GRAIN, ANALYSIS_DATE)
    decomp  = run_decomposition(alert)
    hyps    = generate_hypotheses(decomp)

    evd_map = {}
    rag_map = {}
    build_index()
    for h in hyps:
        if h.fired:
            evd_map[h.rule_id] = retrieve_evidence(h, GRAIN, ANALYSIS_DATE)
            rag_map[h.rule_id] = retrieve_for_hypothesis(h.rule_id, GRAIN, ANALYSIS_DATE)

    scores  = score_all(hyps, evd_map, rag_map, sparse_history=alert.is_sparse_history)
    gate    = evaluate(scores)
    rec     = generate_recommendation(gate, ANALYSIS_DATE, GRAIN)

    bl_narr = generate_narrative(
        "Revenue", (alert.actual_value - alert.expected_value) / alert.expected_value,
        "North / Electronics", gate, scores, evd_map, rag_map, rec,
        ANALYSIS_DATE, persona="business_leader"
    )
    an_narr = generate_narrative(
        "Revenue", (alert.actual_value - alert.expected_value) / alert.expected_value,
        "North / Electronics", gate, scores, evd_map, rag_map, rec,
        ANALYSIS_DATE, persona="analyst"
    )
    return alert, decomp, hyps, evd_map, rag_map, scores, gate, rec, bl_narr, an_narr

# ══════════════════════════════════════════════════════════════════
# SIDEBAR — Login / Persona Selector (§3.1)
# ══════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🔍 TraceIQ")
    st.markdown("---")
    persona = st.radio(
        "View as:", ["Business Leader (North)", "Analyst (All Regions)"],
        index=0, help="In production this is determined by JWT role — see 11_SECURITY_AND_API §2"
    )
    is_analyst = "Analyst" in persona
    st.markdown("---")
    st.markdown("**Region:** North Electronics")
    st.markdown(f"**Date:** {ANALYSIS_DATE}")
    st.markdown("**KPI:** Revenue")

    if st.button("▶ Run Pipeline", help="Triggers detection → decomp → hypotheses → evidence → confidence → gate → recommendation"):
        st.cache_data.clear()
    st.markdown("---")
    st.caption("TraceIQ v1.0 · Stages 2–11 complete")

# ══════════════════════════════════════════════════════════════════
# LOAD DATA
# ══════════════════════════════════════════════════════════════════
with st.spinner("Running TraceIQ pipeline…"):
    try:
        alert, decomp, hyps, evd_map, rag_map, scores, gate, rec, bl_narr, an_narr = _run_pipeline()
        pipeline_ok = True
    except Exception as e:
        st.error(f"Pipeline error: {e}")
        pipeline_ok = False

if not pipeline_ok:
    st.stop()

pct = (alert.actual_value - alert.expected_value) / alert.expected_value
pct_str = f"{pct*100:+.1f}%"

# ══════════════════════════════════════════════════════════════════
# SECTION 1 — KPI Alert Header (§3.3 §1)
# ══════════════════════════════════════════════════════════════════
st.markdown("## Section 1 — KPI Alert")
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    st.markdown(f"### Revenue {pct_str} · North / Electronics")
    status_color = "#ef5350" if alert.status == "INVESTIGATE" else "#42a5f5"
    st.markdown(f'<span style="background:{status_color};color:white;padding:3px 12px;border-radius:12px;font-weight:700">{alert.status}</span>', unsafe_allow_html=True)
    if alert.is_sparse_history:
        st.markdown('<div class="guardrail-warn">⚠️ Limited history — category-level proxy baseline used. Confidence ceiling: Moderate.</div>', unsafe_allow_html=True)
with col2:
    st.metric("Actual", f"£{alert.actual_value:,.0f}")
with col3:
    st.metric("Expected", f"£{alert.expected_value:,.0f}", delta=f"{pct_str}")

# Expected vs Actual chart
fig_kpi = go.Figure()
fig_kpi.add_bar(x=["Expected", "Actual"], y=[alert.expected_value, alert.actual_value],
                marker_color=["#42a5f5", "#ef5350"], name="Revenue")
fig_kpi.add_hline(y=alert.expected_value - 2*alert.historical_std, line_dash="dash",
                  line_color="#78909c", annotation_text="−2σ threshold")
fig_kpi.update_layout(template="plotly_dark", height=260, showlegend=False,
                      title="Revenue: Expected vs Actual (with detection threshold)")
st.plotly_chart(fig_kpi, use_container_width=True)

st.markdown(f"**Z-score:** {alert.zscore:.2f} · **Business impact:** £{alert.business_impact:,.0f} · **Persistence:** {alert.persistence_days} days")

# ══════════════════════════════════════════════════════════════════
# SECTION 2 — Decomposition Tree (§3.3 §2)
# ══════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("## Section 2 — Decomposition Tree")
if decomp:
    df_bars = []
    colors  = []
    for fc in decomp.formula_factors:
        delta = fc.actual - fc.expected
        df_bars.append((fc.factor_name, delta))
        colors.append("#ef5350" if delta < 0 else "#66bb6a")

    if df_bars:
        factors, deltas = zip(*df_bars)
        fig_decomp = go.Figure(go.Bar(
            x=list(factors), y=list(deltas),
            marker_color=colors, text=[f"{d:+,.0f}" for d in deltas], textposition="outside"
        ))
        fig_decomp.update_layout(template="plotly_dark", height=300,
                                  title=f"Revenue decomposition · Dominant factor: {decomp.dominant_factor}",
                                  yaxis_title="Delta from baseline (£)")
        st.plotly_chart(fig_decomp, use_container_width=True)
    st.markdown(f"**Dominant factor:** `{decomp.dominant_factor}` · **Method:** Additive decomposition (07_ANALYTICS §3)")
else:
    st.info("Decomposition not available.")

# ══════════════════════════════════════════════════════════════════
# SECTION 3 — Evidence Graph (§3.3 §3)
# ══════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("## Section 3 — Evidence Graph")
st.caption("Click a hypothesis below to see its evidence trail (Section 4). Each evidence item shows: source, metric, delta, baseline, method, freshness, lineage.")

if scores:
    top = scores[0]
    evd = evd_map.get(top.hypothesis_rule_id)
    if evd and (evd.supporting or evd.contradicting):
        with st.expander(f"Evidence for '{top.driver}' (top hypothesis)" + ("" if not is_analyst else " — expanded"), expanded=is_analyst):
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**✓ Supporting**")
                for item in evd.supporting:
                    st.markdown(f'<div class="evidence-card">📌 <b>{item.metric_name}</b><br>'
                                f'Value: <b>{item.metric_value:.3f}</b> · Baseline: {item.baseline_value:.3f}<br>'
                                f'Delta: <b>{item.delta:+.3f}</b> ({item.delta_pct:+.1f}%)<br>'
                                f'<small>Source: {item.source_table} · Window: {item.window_days}d</small></div>',
                                unsafe_allow_html=True)
            with cols[1]:
                st.markdown("**⚠ Contradicting**")
                if evd.contradicting:
                    for item in evd.contradicting:
                        st.markdown(f'<div class="evidence-card" style="border-color:#ef9a9a">⚠ <b>{item.metric_name}</b><br>'
                                    f'Value: {item.metric_value:.3f} · Baseline: {item.baseline_value:.3f}<br>'
                                    f'Delta: {item.delta:+.3f}<br>'
                                    f'<small>Source: {item.source_table}</small></div>',
                                    unsafe_allow_html=True)
                else:
                    st.markdown("_No contradicting evidence_")

# ══════════════════════════════════════════════════════════════════
# SECTION 4 — Hypothesis Panel (§3.3 §4)
# ══════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("## Section 4 — Hypothesis Panel")

display_scores = scores if is_analyst else scores[:1]
for score in display_scores:
    evd  = evd_map.get(score.hypothesis_rule_id)
    rag  = rag_map.get(score.hypothesis_rule_id)
    with st.container():
        st.markdown(f'<div class="hyp-card">', unsafe_allow_html=True)
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(f"**{score.driver}** — {_badge(score.label)}", unsafe_allow_html=True)
            if is_analyst:
                st.caption(f"Score: {score.raw_score:.3f} · Rule signal: {score.rule_signal_component:.3f} · "
                           f"Structured: {score.structured_evidence_component:.3f} · RAG: {score.rag_component:.3f} · "
                           f"Penalties: −{score.penalties_applied:.3f}")
        with c2:
            st.metric("Confidence", score.label, delta=f"{score.raw_score:.2f}")

        if evd:
            for item in evd.supporting[:3]:
                st.markdown(f"  ✓ {item.description}")
            for item in evd.contradicting[:2]:
                st.markdown(f"  ⚠ {item.description}")

        if rag and rag.snippets:
            top_snip = rag.top_snippet
            if top_snip:
                st.markdown(f"  📄 _{top_snip.text[:120]}…_ (sim={top_snip.similarity_score:.2f})")

        if score.label == "Insufficient":
            st.markdown('<div class="guardrail-warn">🔴 <b>Insufficient Evidence</b> — '
                        'Additional data required before this hypothesis can be actioned.<br>'
                        f'Missing: {"; ".join(gate.missing_data_needed[:2]) if gate.missing_data_needed else "See abstention details."}'
                        '</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# SECTION 5 — Narrative Panel (§3.3 §5)
# ══════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("## Section 5 — Narrative")
st.markdown('<small>🤖 <b>LLM-generated</b> · Language model summarises pre-validated deterministic facts — '
            'it does not perform analysis. <a href="#" title="Evidence/scores computed by SQL+rules (Stages 2-9). '
            'LLM only phrases the result.">ℹ️ What does this mean?</a></small>', unsafe_allow_html=True)

narr = an_narr if is_analyst else bl_narr
st.info(narr.narrative)
if is_analyst:
    with st.expander("Evidence package sent to LLM"):
        import json
        st.json(narr.evidence_package)

# ══════════════════════════════════════════════════════════════════
# SECTION 6 — Action Card (§3.3 §6)
# ══════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("## Section 6 — Action")
st.markdown(f'<div class="action-card">', unsafe_allow_html=True)

if rec.is_actionable:
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**Driver:** {rec.driver}")
        st.markdown(f"**Tier:** `{rec.tier}` recommendation")
        st.markdown(f"**Action owner:** {rec.action_owner}")
        st.markdown(f"**Immediate action:**\n> {rec.immediate_action}")
    with col_b:
        st.markdown(f"**Verification:**\n> {rec.verification_step}")
        st.markdown(f"**Escalation:**\n> {rec.escalation_path}")
        if rec.condition_for_full:
            st.markdown(f"**Before actioning (Conditional):**\n> {rec.condition_for_full}")
else:
    st.warning("⚠️ Insufficient Evidence — no recommendation issued. Collect missing data before acting.")

st.markdown("</div>", unsafe_allow_html=True)
st.markdown("**Human decision:**")
col_fb1, col_fb2, col_fb3, col_fb4 = st.columns(4)
with col_fb1:
    if st.button("✅ Accept", key="accept"):
        st.success("Decision recorded: Accept")
with col_fb2:
    if st.button("❌ Reject", key="reject"):
        st.warning("Decision recorded: Reject")
with col_fb3:
    if st.button("🔍 Need More Evidence", key="more_evd"):
        st.info("Decision recorded: Request more evidence")
with col_fb4:
    if st.button("🔎 Investigate Further", key="inv_further"):
        st.info("Decision recorded: Investigate further")

# ══════════════════════════════════════════════════════════════════
# SECTION 7 — Telemetry Footer (§3.3 §7 — Analyst only)
# ══════════════════════════════════════════════════════════════════
if is_analyst:
    st.markdown("---")
    st.markdown("## Section 7 — Telemetry (Analyst Only)")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("LLM calls", "2")
    t2.metric("Avg latency", f"{narr.llm_response.latency_ms:.0f} ms")
    t3.metric("Total tokens", f"{narr.llm_response.total_tokens}")
    t4.metric("Est. cost", "$0.00")
    st.markdown(f'<div class="telemetry-bar">Provider: {narr.llm_response.provider} · Model: {narr.llm_response.model} · '
                'Caching: enabled · Telemetry storage: Stage 14</div>', unsafe_allow_html=True)
    st.caption("In production: LLM calls logged to telemetry_log table (05_DATA_SCHEMA §3.6).")
