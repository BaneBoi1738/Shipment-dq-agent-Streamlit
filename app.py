"""Public demo: upload a shipment CSV, get a data quality report. Wraps the same engine the AWS Lambda uses."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dq_agent.audit import run_audit  # noqa: E402
from dq_agent.config import load_config  # noqa: E402
from dq_agent.report import to_markdown  # noqa: E402
from dq_agent.s3_handler import InputError, read_csv_bytes  # noqa: E402

MAX_ROWS = 50_000
st.set_page_config(page_title="Break my data quality checker", page_icon="🔍", layout="wide")


def decode_to_utf8(body: bytes) -> tuple[bytes, str]:
    """Try UTF-8 (with BOM), fall back to Latin-1 so accented files don't just fail."""
    try:
        body.decode("utf-8-sig")
        return body, "utf-8"
    except UnicodeDecodeError:
        return body.decode("latin-1").encode("utf-8"), "latin-1 (converted)"


@st.cache_data(show_spinner=False)
def audit_bytes(body: bytes, name: str):
    body, enc = decode_to_utf8(body)
    df = read_csv_bytes(body)  # raises InputError for empty / header-only / unparseable
    if len(df) > MAX_ROWS:
        raise InputError(f"file has {len(df):,} rows; this public demo is capped at {MAX_ROWS:,}")
    result = run_audit(df, load_config(), source=name)
    return result, enc


st.title("🔍 Break my data quality checker")
st.write(
    "Upload a shipment CSV (any column names, any order) and I'll tell you what's wrong with it: "
    "missing IDs, duplicates, bad dates, impossible timelines. "
    "**Your ugliest file wins.** Same engine as my AWS S3 → Lambda pipeline."
)
st.caption(f"Up to {MAX_ROWS:,} rows / 10 MB. Files are processed in memory and not stored.")

col_a, col_b = st.columns([3, 1])
up = col_a.file_uploader("Drop a CSV", type=["csv"])
use_sample = col_b.button("Try a messy sample")

body, name = None, None
if up is not None:
    body, name = up.getvalue(), up.name
elif use_sample:
    sample = Path(__file__).parent / "data" / "shipments_messy.csv"
    if sample.exists():
        body, name = sample.read_bytes(), sample.name
    else:
        st.warning("Sample file missing (data/shipments_messy.csv).")

if body is not None:
    try:
        result, enc = audit_bytes(body, name)
    except InputError as exc:
        st.error(f"Can't audit this file: {exc}")
        st.caption("This is the same non-retryable input-rejection path the Lambda uses (it writes an .error.json).")
        st.stop()

    m = result.metrics
    icon = {"PASS": "✅", "WARN": "🟠", "FAIL": "🔴"}[result.status]
    st.subheader(f"{icon} {result.status}   ·   SLA {'met' if m['sla']['met'] else 'NOT met'} (≥{m['sla']['min_clean_rate']:.0%} clean rows)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total records", f"{m['total_records']:,}")
    c2.metric("Valid", f"{m['valid_records']:,}")
    c3.metric("Invalid", f"{m['invalid_records']:,}")
    c4.metric("Error rate", f"{m['error_rate']:.1%}")
    if enc != "utf-8":
        st.info(f"File wasn't UTF-8; decoded as {enc}.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Errors by type")
        if m["errors_by_type"]:
            s = pd.Series(m["errors_by_type"], name="rows").sort_values(ascending=False)
            st.bar_chart(s)
        else:
            st.success("No row-level errors.")
        if m["dataset_issues"]:
            st.warning("Dataset-level issues: " + ", ".join(m["dataset_issues"]))
    with right:
        st.markdown("#### Schema mapping")
        if result.schema.renamed:
            st.dataframe(pd.DataFrame([{"your column": r.source, "mapped to": r.canonical, "method": r.method,
                                        "score": round(r.score, 2)} for r in result.schema.renamed]),
                         hide_index=True, use_container_width=True)
        else:
            st.write("All columns matched exactly.")
        if result.schema.missing_required:
            st.error("Missing required columns: " + ", ".join(result.schema.missing_required))

    st.markdown("#### Findings")
    for f in result.findings:
        box = st.error if f.severity == "critical" else st.warning
        box(f"**{f.check}** ({f.severity}): {f.message}  — {f.count:,} affected")

    st.markdown("#### Download")
    d1, d2, d3 = st.columns(3)
    d1.download_button("Report (.md)", to_markdown(result), "dq_report.md", "text/markdown")
    d2.download_button("Metrics (.json)", json.dumps(result.to_dict(), indent=2), "dq_report.json", "application/json")
    d3.download_button("Row-level errors (.csv)", result.error_details_csv(), "dq_errors.csv", "text/csv")

st.divider()
st.caption("Built with Python, pandas and Streamlit. Engine: github.com/BaneBoi1738/shipment-dq-agent. "
           "Built with AI assistance; tests: 68 passing.")
