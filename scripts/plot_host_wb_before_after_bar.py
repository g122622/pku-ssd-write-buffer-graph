#!/usr/bin/env python3
"""
Bar-chart comparison for WB-HOST logs (before vs after optimization).

Use average values to compare logs with different sample counts.

Inputs:
- results/host-log/before-optimization.log
- results/host-log/after-optimazion.log

Outputs:
- output-figures/host_wb_before_after_bar.png
- output-csv/host_wb_before_after_avg.csv
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional

import matplotlib.pyplot as plt


WB_RE = re.compile(
    r"wb_work perf\(1s\):\s*"
    r"calls=(?P<calls>\d+)\s+"
    r"segs=(?P<segs>\d+)\s+"
    r"bytes=(?P<bytes>\d+)\s+"
    r"avg=(?P<avg>[\d.]+)us\s*\|\s*"
    r"queue_wait=(?P<queue_wait>[\d.]+)us\s+"
    r"mcp_read=(?P<mcp_read>[\d.]+)us\s+"
    r"parse=(?P<parse>[\d.]+)us\s+"
    r"copy=(?P<copy>[\d.]+)us\s+"
    r"(?:(?:notify=(?P<notify>[\d.]+)us\s+)?)"
    r"complete=(?P<complete>[\d.]+)us"
)

NOTIFY_RE = re.compile(
    r"notify_calls=(?P<notify_calls>\d+)\s+"
    r"notify_qwait=(?P<notify_qwait>[\d.]+)us\s+"
    r"notify_submit=(?P<notify_submit>[\d.]+)us\s+"
    r"notify_batch_calls=(?P<notify_batch_calls>\d+)\s+"
    r"notify_batch_entries=(?P<notify_batch_entries>\d+)\s+"
    r"notify_batch_fill=(?P<notify_batch_fill>[\d.]+)"
)


def parse_log(path: Path) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = WB_RE.search(line)
        if not m:
            continue

        g = m.groupdict()
        row: Dict[str, float] = {
            "calls": float(g["calls"]),
            "segs": float(g["segs"]),
            "bytes": float(g["bytes"]),
            "avg_us": float(g["avg"]),
            "queue_wait_us": float(g["queue_wait"]),
            "mcp_read_us": float(g["mcp_read"]),
            "parse_us": float(g["parse"]),
            "copy_us": float(g["copy"]),
            "complete_us": float(g["complete"]),
            "notify_us": float(g["notify"]) if g.get("notify") else 0.0,
        }

        # 1-second window -> bytes/s directly
        row["throughput_mib_s"] = row["bytes"] / (1024.0)

        nm = NOTIFY_RE.search(line)
        if nm:
            ng = nm.groupdict()
            row["notify_calls"] = float(ng["notify_calls"])
            row["notify_qwait_us"] = float(ng["notify_qwait"])
            row["notify_submit_us"] = float(ng["notify_submit"])
            row["notify_batch_calls"] = float(ng["notify_batch_calls"])
            row["notify_batch_entries"] = float(ng["notify_batch_entries"])
            row["notify_batch_fill"] = float(ng["notify_batch_fill"])
            # 折算每个完成段的通知提交开销（便于和旧 notify 大致同量纲比较）
            if row["segs"] > 0:
                row["notify_submit_per_seg_us"] = row["notify_submit_us"] * row["notify_calls"] / row["segs"]
                row["notify_qwait_per_seg_us"] = row["notify_qwait_us"] * row["notify_calls"] / row["segs"]
        rows.append(row)

    return rows


def avg_of(rows: List[Dict[str, float]], key: str) -> Optional[float]:
    vals = [r[key] for r in rows if key in r]
    if not vals:
        return None
    return mean(vals)


def summarize(rows: List[Dict[str, float]]) -> Dict[str, float]:
    keys = [
        "calls",
        "segs",
        "throughput_mib_s",
        "avg_us",
        "queue_wait_us",
        "mcp_read_us",
        "parse_us",
        "copy_us",
        "complete_us",
        "notify_us",
        "notify_submit_per_seg_us",
        "notify_qwait_per_seg_us",
    ]
    out: Dict[str, float] = {"samples": float(len(rows))}
    for k in keys:
        v = avg_of(rows, k)
        if v is not None:
            out[k] = v
    return out


def save_summary_csv(before: Dict[str, float], after: Dict[str, float], out_csv: Path) -> None:
    metric_order = [
        "samples",
        "calls",
        "segs",
        "throughput_mib_s",
        "avg_us",
        "queue_wait_us",
        "mcp_read_us",
        "parse_us",
        "copy_us",
        "complete_us",
        "notify_us",
        "notify_submit_per_seg_us",
        "notify_qwait_per_seg_us",
    ]

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "before_avg", "after_avg", "change_pct"])
        for m in metric_order:
            b = before.get(m)
            a = after.get(m)
            if b is None and a is None:
                continue
            if b is None:
                change = ""
            elif b == 0:
                change = ""
            elif a is None:
                change = ""
            else:
                change = f"{(a - b) / b * 100.0:.2f}"
            writer.writerow([
                m,
                "" if b is None else f"{b:.6f}",
                "" if a is None else f"{a:.6f}",
                change,
            ])


def plot_bar(before: Dict[str, float], after: Dict[str, float], out_png: Path) -> None:
    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        plt.style.use("ggplot")

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), squeeze=False)
    fig.suptitle("WB-HOST Before/After Comparison (Averages)", fontsize=16, fontweight="bold", y=0.995)

    # Left: latency breakdown
    latency_metrics = ["avg_us", "queue_wait_us", "mcp_read_us", "parse_us", "copy_us", "complete_us"]
    labels = ["avg", "queue_wait", "mcp_read", "parse", "copy", "complete"]
    b = [before.get(m, 0.0) for m in latency_metrics]
    a = [after.get(m, 0.0) for m in latency_metrics]

    ax = axes[0, 0]
    x = list(range(len(labels)))
    w = 0.38
    ax.bar([i - w / 2 for i in x], b, width=w, color="#E45756", label="before")
    ax.bar([i + w / 2 for i in x], a, width=w, color="#54A24B", label="after")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20)
    ax.set_yscale("log")
    ax.set_ylabel("Latency (us, log)")
    ax.set_title("Core latency metrics")
    ax.legend(loc="best", fontsize=8)

    # annotate reduction for latency
    for i, (bv, av) in enumerate(zip(b, a)):
        if bv > 0 and av > 0:
            reduction_pct = (bv - av) / bv * 100.0
            reduction_ratio = bv / av
            ax.text(i + w / 2, av * 0.7, f"↓{reduction_pct:.0f}%\n(÷{reduction_ratio:.1f}x)", 
                    ha="center", va="bottom", fontsize=9, color="black")

    # Right: throughput and parallelism
    perf_metrics = ["throughput_mib_s", "calls", "segs"]
    perf_labels = ["MiB/s", "calls/s", "segs/s"]
    b2 = [before.get(m, 0.0) for m in perf_metrics]
    a2 = [after.get(m, 0.0) for m in perf_metrics]

    ax = axes[0, 1]
    x2 = list(range(len(perf_labels)))
    ax.bar([i - w / 2 for i in x2], b2, width=w, color="#E45756", label="before")
    ax.bar([i + w / 2 for i in x2], a2, width=w, color="#54A24B", label="after")
    ax.set_xticks(x2)
    ax.set_xticklabels(perf_labels)
    ax.set_title("Throughput / processing rate")

    # annotate gain for throughput
    for i, (bv, av) in enumerate(zip(b2, a2)):
        if bv > 0:
            gain = av / bv
            ax.text(i, max(bv, av) * 1.02, f"x{gain:.2f}", ha="center", va="bottom", fontsize=9)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    before_log = repo_root / "results" / "host-log" / "before-optimization.log"
    after_log = repo_root / "results" / "host-log" / "after-optimazion.log"

    before_rows = parse_log(before_log)
    after_rows = parse_log(after_log)
    if not before_rows or not after_rows:
        print("No valid wb_work perf lines found.")
        return

    before_avg = summarize(before_rows)
    after_avg = summarize(after_rows)

    out_png = repo_root / "output-figures" / "host_wb_before_after_bar.png"
    out_csv = repo_root / "output-csv" / "host_wb_before_after_avg.csv"

    save_summary_csv(before_avg, after_avg, out_csv)
    plot_bar(before_avg, after_avg, out_png)

    print(f"Saved: {out_csv}")
    print(f"Saved: {out_png}")
    print(
        "summary: "
        f"avg_us before={before_avg.get('avg_us', 0):.2f} after={after_avg.get('avg_us', 0):.2f}; "
        f"KiB/s before={before_avg.get('throughput_mib_s', 0):.2f} after={after_avg.get('throughput_mib_s', 0):.2f}"
    )


if __name__ == "__main__":
    main()
