"""Operator UI for the Incident Triage Agent.

A thin Streamlit client over the FastAPI backend: every action here (start
incident, step, run, approve/deny) is a real HTTP call against the agent
runtime API, not local state — so what you see here is exactly the persisted,
resumable state the backend holds.
"""
from __future__ import annotations

import os
import time

import requests
import streamlit as st

API_BASE = os.environ.get("TRIAGE_API_BASE", "http://127.0.0.1:8000")

st.set_page_config(page_title="Incident Triage Agent", layout="wide")

STEP_ICONS = {
    "system": "⚙️",
    "plan": "🧠",
    "tool_call": "🔧",
    "tool_result": "✅",
    "tool_error": "⚠️",
    "reflection": "💭",
    "approval_request": "🙋",
    "approval_decision": "👤",
    "resolution": "🏁",
}


def api_get(path: str):
    return requests.get(f"{API_BASE}{path}", timeout=10).json()


def api_post(path: str, json: dict | None = None):
    r = requests.post(f"{API_BASE}{path}", json=json, timeout=30)
    r.raise_for_status()
    return r.json()


def render_transcript(run: dict) -> None:
    for s in run["transcript"]:
        icon = STEP_ICONS.get(s["type"], "•")
        label = s["type"].replace("_", " ").title()
        with st.chat_message("assistant" if s["type"] != "approval_decision" else "user"):
            st.markdown(f"**{icon} {label}**")
            st.write(s["content"])
            if s.get("tool_name"):
                st.caption(f"tool: `{s['tool_name']}` args: `{s.get('tool_args')}`")


def render_run(run: dict) -> None:
    status = run["status"]
    status_color = {
        "planning": "blue",
        "running": "blue",
        "awaiting_approval": "orange",
        "resolved": "green",
        "failed": "red",
    }.get(status, "gray")

    st.markdown(f"### Incident: {run['incident']}")
    st.markdown(f":{status_color}[**{status.upper()}**]  ·  run `{run['id']}`")

    col1, col2, col3 = st.columns(3)
    with col1:
        if status == "running" and st.button("▶ Step once", use_container_width=True):
            api_post(f"/runs/{run['id']}/step")
            st.rerun()
    with col2:
        if status == "running" and st.button("⏩ Run autonomously", use_container_width=True):
            api_post(f"/runs/{run['id']}/run")
            st.rerun()
    with col3:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    if status == "awaiting_approval" and run.get("pending_action"):
        pa = run["pending_action"]
        st.warning(
            f"**Agent is requesting approval to act**\n\n"
            f"Action: `{pa['tool_name']}({pa['tool_args']})`\n\nReason: {pa['reason']}"
        )
        note = st.text_input("Note to agent (optional)", key=f"note_{run['id']}")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Approve", type="primary", use_container_width=True, key=f"approve_{run['id']}"):
                api_post(f"/runs/{run['id']}/decision", {"approve": True, "note": note or None})
                st.rerun()
        with c2:
            if st.button("❌ Deny", use_container_width=True, key=f"deny_{run['id']}"):
                api_post(f"/runs/{run['id']}/decision", {"approve": False, "note": note or None})
                st.rerun()

    st.divider()
    st.markdown("#### Transcript")
    render_transcript(run)


def main() -> None:
    st.title("🚨 Incident Triage Agent — Operator Console")

    try:
        api_get("/health")
    except requests.exceptions.RequestException:
        st.error(f"Can't reach backend at {API_BASE}. Is `uvicorn app.main:app` running?")
        return

    with st.sidebar:
        st.header("New incident")
        with st.form("new_incident"):
            description = st.text_area(
                "Describe the incident",
                placeholder="checkout-service p99 latency spiking, 5xx rate climbing",
            )
            submitted = st.form_submit_button("Start triage", type="primary")
        if submitted and description.strip():
            run = api_post("/incidents", {"description": description.strip()})
            st.session_state["selected_run"] = run["id"]
            st.rerun()

        st.divider()
        st.header("Runs")
        runs = api_get("/runs")
        for r in runs:
            label = f"{r['status']} · {r['incident'][:30]}"
            if st.button(label, key=f"select_{r['id']}", use_container_width=True):
                st.session_state["selected_run"] = r["id"]
                st.rerun()

        st.divider()
        auto = st.checkbox("Live mode (auto-refresh every 2s)")

    selected = st.session_state.get("selected_run")
    if not selected and runs:
        selected = runs[0]["id"]

    if not selected:
        st.info("Start a new incident from the sidebar to see the agent work.")
        return

    run = api_get(f"/runs/{selected}")
    render_run(run)

    if auto and run["status"] == "running":
        time.sleep(2)
        st.rerun()


if __name__ == "__main__":
    main()
