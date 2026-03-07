#!/usr/bin/env python3
"""
Plot controller write-buffer performance logs and highlight bottlenecks.

Data source:
- results/controller-log-main/main.log
- results/controller-log-subpart/subpart.log

Outputs:
- output-figures/controller_wb_bottleneck_dashboard.png
- output-csv/controller_wb_main_timeseries.csv
- output-csv/controller_wb_subpart_timeseries.csv
- output-csv/controller_wb_subpart_pareto.csv
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional

import matplotlib.pyplot as plt


MAIN_RE = re.compile(
    r"WB perf\(1s\):\s*"
    r"stage calls=(?P<stage_calls>\d+) ok=(?P<stage_ok>\d+) avg=(?P<stage_avg>[\d.]+)us bw=(?P<stage_bw>[\d.]+)MiB/s\s*\|\s*"
    r"copy_done calls=(?P<copy_calls>\d+) segs=(?P<copy_segs>\d+) avg=(?P<copy_avg>[\d.]+)us\s*\|\s*"
    r"read_done calls=(?P<read_calls>\d+) segs=(?P<read_segs>\d+) avg=(?P<read_avg>[\d.]+)us\s*\|\s*"
    r"flush calls=(?P<flush_calls>\d+) segs=(?P<flush_segs>\d+) avg=(?P<flush_avg>[\d.]+)us bw=(?P<flush_bw>[\d.]+)MiB/s"
)

SUBPART_RE = re.compile(
    r"WB copy_done perf\(1s\):\s*"
    r"calls=(?P<calls>\d+) segs=(?P<segs>\d+) avg=(?P<avg>[\d.]+)us\s*\|\s*"
    r"lock_wait=(?P<lock_wait>[\d.]+)us\s*"
    r"lock_hold=(?P<lock_hold>[\d.]+)us\s*"
    r"lookup=(?P<lookup>[\d.]+)us\s*"
    r"mcp_release=(?P<mcp_release>[\d.]+)us\s*"
    r"loop=(?P<loop>[\d.]+)us\s*"
    r"mirror=(?P<mirror>[\d.]+)us\s*"
    r"index=(?P<index>[\d.]+)us\s*"
    r"reclaim=(?P<reclaim>[\d.]+)us\s*"
    r"rm_track=(?P<rm_track>[\d.]+)us"
)


def to_float(d: Dict[str, str], key: str) -> float:
    return float(d[key])


def to_int(d: Dict[str, str], key: str) -> int:
    return int(d[key])


def parse_main_log(path: Path) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = MAIN_RE.search(line)
        if not m:
            continue
        g = m.groupdict()
        rows.append(
            {
                "time_sec": float(len(rows) + 1),
                "stage_calls": float(to_int(g, "stage_calls")),
                "stage_ok": float(to_int(g, "stage_ok")),
                "stage_avg_us": to_float(g, "stage_avg"),
                "stage_bw_mib": to_float(g, "stage_bw"),
                "copy_calls": float(to_int(g, "copy_calls")),
                "copy_segs": float(to_int(g, "copy_segs")),
                "copy_avg_us": to_float(g, "copy_avg"),
                "flush_calls": float(to_int(g, "flush_calls")),
                "flush_segs": float(to_int(g, "flush_segs")),
                "flush_avg_us": to_float(g, "flush_avg"),
                "flush_bw_mib": to_float(g, "flush_bw"),
            }
        )

    for r in rows:
        stage_avg = r["stage_avg_us"]
        copy_avg = r["copy_avg_us"]
        flush_avg = r["flush_avg_us"] if r["flush_calls"] > 0 else 0.0

        if flush_avg >= max(stage_avg, copy_avg) and r["flush_calls"] > 0:
            r["bottleneck_code"] = 2.0
        elif copy_avg >= stage_avg:
            r["bottleneck_code"] = 1.0
        else:
            r["bottleneck_code"] = 0.0

    return rows


def parse_subpart_log(path: Path) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = SUBPART_RE.search(line)
        if not m:
            continue
        g = m.groupdict()
        rows.append(
            {
                "time_sec": float(len(rows) + 1),
                "calls": float(to_int(g, "calls")),
                "segs": float(to_int(g, "segs")),
                "avg_us": to_float(g, "avg"),
                "lock_wait_us": to_float(g, "lock_wait"),
                "lock_hold_us": to_float(g, "lock_hold"),
                "lookup_us": to_float(g, "lookup"),
                "mcp_release_us": to_float(g, "mcp_release"),
                "loop_us": to_float(g, "loop"),
                "mirror_us": to_float(g, "mirror"),
                "index_us": to_float(g, "index"),
                "reclaim_us": to_float(g, "reclaim"),
                "rm_track_us": to_float(g, "rm_track"),
            }
        )
    return rows


def moving_average(values: List[float], window: int = 5) -> List[float]:
    if not values:
        return []
    w = max(1, window)
    out: List[float] = []
    q: List[float] = []
    acc = 0.0
    for v in values:
        q.append(v)
        acc += v
        if len(q) > w:
            acc -= q.pop(0)
        out.append(acc / len(q))
    return out


def save_csv(rows: List[Dict[str, float]], out_csv: Path, field_order: List[str]) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=field_order)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in field_order})


def make_pareto(sub_rows: List[Dict[str, float]]) -> List[Dict[str, float]]:
    component_cols = [
        "lock_hold_us",
        "loop_us",
        "lock_wait_us",
        "mirror_us",
        "index_us",
        "lookup_us",
        "rm_track_us",
        "mcp_release_us",
        "reclaim_us",
    ]

    medians = []
    for col in component_cols:
        vals = [r[col] for r in sub_rows]
        medians.append((col, median(vals) if vals else 0.0))

    medians.sort(key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in medians) or 1.0

    out: List[Dict[str, float]] = []
    cum = 0.0
    for name, med in medians:
        pct = med / total * 100.0
        cum += pct
        out.append(
            {
                "component": name,
                "median_us": med,
                "share_pct": pct,
                "cum_share_pct": cum,
            }
        )
    return out


def plot_dashboard(main_rows: List[Dict[str, float]], sub_rows: List[Dict[str, float]], pareto: List[Dict[str, float]], output_png: Path) -> None:
    if not main_rows or not sub_rows:
        print("No valid records parsed from controller logs.")
        return

    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        plt.style.use("ggplot")

    fig, axes = plt.subplots(2, 2, figsize=(15, 10), squeeze=False)
    fig.suptitle("Controller WB Bottleneck Dashboard", fontsize=16, fontweight="bold", y=0.995)
    fig.text(0.5, 0.975, "Data source: results/controller-log-main/main.log + controller-log-subpart/subpart.log", ha="center", va="top", fontsize=9)

    t_main = [r["time_sec"] for r in main_rows]
    stage_bw = [r["stage_bw_mib"] for r in main_rows]
    flush_bw = [r["flush_bw_mib"] for r in main_rows]

    stage_avg = [r["stage_avg_us"] for r in main_rows]
    copy_avg = [r["copy_avg_us"] for r in main_rows]
    flush_avg = [r["flush_avg_us"] if r["flush_calls"] > 0 else math.nan for r in main_rows]

    # A) Main throughput trend + flush windows
    ax = axes[0, 0]
    ax.plot(t_main, stage_bw, color="#4C78A8", linewidth=2.0, label="stage BW (MiB/s)")
    ax.plot(t_main, moving_average(stage_bw, 5), color="#4C78A8", linewidth=1.3, linestyle="--", alpha=0.8, label="stage BW MA(5)")
    ax.plot(t_main, flush_bw, color="#E45756", linewidth=1.8, alpha=0.9, label="flush BW (MiB/s)")
    for i, r in enumerate(main_rows):
        if r["flush_calls"] > 0:
            x = t_main[i]
            ax.axvspan(x - 0.5, x + 0.5, color="#E45756", alpha=0.14)
    ax.set_title("Main log: Throughput timeline")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("MiB/s")
    ax.legend(loc="best", fontsize=8)

    # B) Main latency trend (log scale to expose flush spikes)
    ax = axes[0, 1]
    ax.plot(t_main, stage_avg, color="#72B7B2", linewidth=1.8, label="stage avg (us)")
    ax.plot(t_main, copy_avg, color="#F58518", linewidth=2.0, label="copy_done avg (us)")
    ax.plot(t_main, flush_avg, color="#E45756", linewidth=1.8, marker="o", markersize=3, label="flush avg (us)")
    ax.set_yscale("log")
    ax.set_title("Main log: Latency timeline (log-scale)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Latency (us, log)")
    ax.legend(loc="best", fontsize=8)

    # C) Subpart key-component trend
    t_sub = [r["time_sec"] for r in sub_rows]
    ax = axes[1, 0]
    ax.plot(t_sub, [r["avg_us"] for r in sub_rows], color="black", linewidth=2.2, label="copy_done avg")
    ax.plot(t_sub, [r["lock_hold_us"] for r in sub_rows], color="#E45756", linewidth=2.0, label="lock_hold")
    ax.plot(t_sub, [r["loop_us"] for r in sub_rows], color="#B279A2", linewidth=1.7, label="loop")
    ax.plot(t_sub, [r["lock_wait_us"] for r in sub_rows], color="#4C78A8", linewidth=1.7, label="lock_wait")
    ax.plot(t_sub, [r["mirror_us"] for r in sub_rows], color="#54A24B", linewidth=1.5, label="mirror")
    ax.set_title("Subpart log: Key latency components")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Latency (us)")
    ax.legend(loc="best", fontsize=8)

    # D) Pareto chart for bottleneck decomposition
    ax = axes[1, 1]
    labels = [p["component"].replace("_us", "") for p in pareto]
    med = [p["median_us"] for p in pareto]
    cum = [p["cum_share_pct"] for p in pareto]

    bars = ax.bar(labels, med, color="#4C78A8", alpha=0.85, label="median latency")
    ax.set_title("Subpart log: Pareto of median component latency")
    ax.set_xlabel("Component")
    ax.set_ylabel("Median latency (us)")
    ax.tick_params(axis="x", rotation=35)

    ax2 = ax.twinx()
    ax2.plot(labels, cum, color="#E45756", marker="o", linewidth=2.0, label="cumulative share")
    ax2.axhline(80.0, color="#E45756", linestyle="--", linewidth=1.2, alpha=0.7)
    ax2.set_ylabel("Cumulative share (%)")
    ax2.set_ylim(0, 105)

    if bars:
        top_idx = max(range(len(med)), key=lambda i: med[i])
        bars[top_idx].set_color("#F58518")
        bars[top_idx].set_alpha(0.95)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output_png}")


def print_summary(main_rows: List[Dict[str, float]], pareto: List[Dict[str, float]]) -> None:
    copy_med = median([r["copy_avg_us"] for r in main_rows]) if main_rows else 0.0
    stage_med = median([r["stage_avg_us"] for r in main_rows]) if main_rows else 0.0
    flush_events = sum(1 for r in main_rows if r["flush_calls"] > 0)

    print(f"main.log median latency: stage={stage_med:.2f}us, copy_done={copy_med:.2f}us")
    print(f"main.log flush-active seconds: {flush_events}/{len(main_rows)}")

    if pareto:
        top = pareto[0]
        print(
            "subpart.log top bottleneck: "
            f"{top['component']} (median={top['median_us']:.2f}us, share={top['share_pct']:.1f}%)"
        )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    main_log = repo_root / "results" / "controller-log-main" / "main.log"
    subpart_log = repo_root / "results" / "controller-log-subpart" / "subpart.log"

    if not main_log.exists() or not subpart_log.exists():
        print("controller logs not found.")
        return

    main_rows = parse_main_log(main_log)
    sub_rows = parse_subpart_log(subpart_log)

    if not main_rows:
        print(f"No valid WB perf lines parsed from: {main_log}")
        return
    if not sub_rows:
        print(f"No valid WB copy_done perf lines parsed from: {subpart_log}")
        return

    pareto = make_pareto(sub_rows)

    out_fig = repo_root / "output-figures" / "controller_wb_bottleneck_dashboard.png"
    out_main_csv = repo_root / "output-csv" / "controller_wb_main_timeseries.csv"
    out_sub_csv = repo_root / "output-csv" / "controller_wb_subpart_timeseries.csv"
    out_pareto_csv = repo_root / "output-csv" / "controller_wb_subpart_pareto.csv"

    save_csv(
        main_rows,
        out_main_csv,
        [
            "time_sec",
            "stage_calls",
            "stage_ok",
            "stage_avg_us",
            "stage_bw_mib",
            "copy_calls",
            "copy_segs",
            "copy_avg_us",
            "flush_calls",
            "flush_segs",
            "flush_avg_us",
            "flush_bw_mib",
            "bottleneck_code",
        ],
    )
    save_csv(
        sub_rows,
        out_sub_csv,
        [
            "time_sec",
            "calls",
            "segs",
            "avg_us",
            "lock_wait_us",
            "lock_hold_us",
            "lookup_us",
            "mcp_release_us",
            "loop_us",
            "mirror_us",
            "index_us",
            "reclaim_us",
            "rm_track_us",
        ],
    )

    out_pareto_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_pareto_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["component", "median_us", "share_pct", "cum_share_pct"])
        writer.writeheader()
        for r in pareto:
            writer.writerow(r)

    print(f"Saved: {out_main_csv}")
    print(f"Saved: {out_sub_csv}")
    print(f"Saved: {out_pareto_csv}")

    plot_dashboard(main_rows, sub_rows, pareto, out_fig)
    print_summary(main_rows, pareto)


if __name__ == "__main__":
    main()
