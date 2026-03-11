#!/usr/bin/env python3
"""
Plot host-side write-buffer performance logs and highlight bottlenecks.

Data source:
- results/host-log/before-optimization.log

Outputs:
- output-figures/host_wb_bottleneck_dashboard.png
- output-csv/host_wb_timeseries.csv
- output-csv/host_wb_component_pareto.csv
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from statistics import median
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt


HOST_RE = re.compile(
    r"\[\s*(?P<kernel_ts>[\d.]+)\]\s+.*?wb_work perf\(1s\):\s*"
    r"calls=(?P<calls>\d+)\s+"
    r"segs=(?P<segs>\d+)\s+"
    r"bytes=(?P<bytes>\d+)\s+"
    r"avg=(?P<avg>[\d.]+)us\s*\|\s*"
    r"queue_wait=(?P<queue_wait>[\d.]+)us\s+"
    r"mcp_read=(?P<mcp_read>[\d.]+)us\s+"
    r"parse=(?P<parse>[\d.]+)us\s+"
    r"copy=(?P<copy>[\d.]+)us\s+"
    r"(?:notify=(?P<notify>[\d.]+)us\s+)?"
    r"complete=(?P<complete>[\d.]+)us\s*\|\s*"
    r"mcp_ready=(?P<mcp_ready>\d+)\s+"
    r"parse_fail=(?P<parse_fail>\d+)\s+"
    r"copy_done=(?P<copy_done>\d+)\s+"
    r"read_done=(?P<read_done>\d+)\s+"
    r"fallback=(?P<fallback>\d+)\s+"
    r"mode=(?P<mode>\S+)"
)


BOTTLENECK_COMPONENTS: List[Tuple[str, str]] = [
    ("queue_wait_us", "queue_wait"),
    ("notify_us", "notify"),
    ("complete_us", "complete"),
    ("copy_us", "copy"),
    ("mcp_read_us", "mcp_read"),
    ("parse_us", "parse"),
]


def moving_average(values: List[float], window: int = 5) -> List[float]:
    if not values:
        return []
    queue: List[float] = []
    acc = 0.0
    out: List[float] = []
    for value in values:
        queue.append(value)
        acc += value
        if len(queue) > max(1, window):
            acc -= queue.pop(0)
        out.append(acc / len(queue))
    return out


def parse_host_log(path: Path) -> List[Dict[str, float | str]]:
    rows: List[Dict[str, float | str]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = HOST_RE.search(line)
        if not match:
            continue

        groups = match.groupdict()
        kernel_ts = float(groups["kernel_ts"])
        row: Dict[str, float | str] = {
            "kernel_ts_sec": kernel_ts,
            "time_sec": float(len(rows) + 1),
            "calls": float(groups["calls"]),
            "segs": float(groups["segs"]),
            "bytes": float(groups["bytes"]),
            "throughput_mib_s": float(groups["bytes"]) / (1024.0 * 1024.0),
            "avg_us": float(groups["avg"]),
            "queue_wait_us": float(groups["queue_wait"]),
            "mcp_read_us": float(groups["mcp_read"]),
            "parse_us": float(groups["parse"]),
            "copy_us": float(groups["copy"]),
            "notify_us": float(groups["notify"]) if groups.get("notify") else 0.0,
            "complete_us": float(groups["complete"]),
            "mcp_ready": float(groups["mcp_ready"]),
            "parse_fail": float(groups["parse_fail"]),
            "copy_done": float(groups["copy_done"]),
            "read_done": float(groups["read_done"]),
            "fallback": float(groups["fallback"]),
            "mode": groups["mode"],
        }
        rows.append(row)

    if not rows:
        return rows

    start_ts = float(rows[0]["kernel_ts_sec"])
    component_code = {label: float(index) for index, (_, label) in enumerate(BOTTLENECK_COMPONENTS)}

    for row in rows:
        row["elapsed_sec"] = float(row["kernel_ts_sec"]) - start_ts
        dominant_key, dominant_label = max(
            BOTTLENECK_COMPONENTS,
            key=lambda item: float(row[item[0]]),
        )
        row["bottleneck_component"] = dominant_label
        row["bottleneck_value_us"] = float(row[dominant_key])
        row["bottleneck_code"] = component_code[dominant_label]
        total_component = sum(float(row[key]) for key, _ in BOTTLENECK_COMPONENTS)
        row["queue_wait_share_pct"] = float(row["queue_wait_us"]) / total_component * 100.0 if total_component else 0.0
        row["notify_share_pct"] = float(row["notify_us"]) / total_component * 100.0 if total_component else 0.0

    return rows


def save_csv(rows: List[Dict[str, float | str]], out_csv: Path, field_order: List[str]) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=field_order)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in field_order})


def make_pareto(rows: List[Dict[str, float | str]]) -> List[Dict[str, float | str]]:
    medians: List[Tuple[str, float]] = []
    for key, label in BOTTLENECK_COMPONENTS:
        values = [float(row[key]) for row in rows]
        medians.append((label, median(values) if values else 0.0))

    medians.sort(key=lambda item: item[1], reverse=True)
    total = sum(value for _, value in medians) or 1.0

    output: List[Dict[str, float | str]] = []
    cumulative = 0.0
    for label, med_value in medians:
        share = med_value / total * 100.0
        cumulative += share
        output.append(
            {
                "component": label,
                "median_us": med_value,
                "share_pct": share,
                "cum_share_pct": cumulative,
            }
        )
    return output


def plot_dashboard(
    rows: List[Dict[str, float | str]],
    pareto: List[Dict[str, float | str]],
    output_png: Path,
) -> None:
    if not rows:
        print("No valid records parsed from host log.")
        return

    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        plt.style.use("ggplot")

    fig, axes = plt.subplots(2, 2, figsize=(15, 10), squeeze=False)
    fig.suptitle("Host WB Bottleneck Dashboard", fontsize=16, fontweight="bold", y=0.995)
    fig.text(
        0.5,
        0.975,
        "Data source: results/host-log/before-optimization.log",
        ha="center",
        va="top",
        fontsize=9,
    )

    x = [float(row["elapsed_sec"]) for row in rows]
    throughput = [float(row["throughput_mib_s"]) for row in rows]
    calls = [float(row["calls"]) for row in rows]
    avg_us = [float(row["avg_us"]) for row in rows]
    queue_wait = [float(row["queue_wait_us"]) for row in rows]
    notify = [float(row["notify_us"]) for row in rows]
    complete = [float(row["complete_us"]) for row in rows]
    copy = [float(row["copy_us"]) for row in rows]

    bottleneck_labels = [label for _, label in BOTTLENECK_COMPONENTS]
    bottleneck_codes = [float(row["bottleneck_code"]) for row in rows]

    ax = axes[0, 0]
    ax.plot(x, throughput, color="#4C78A8", linewidth=2.0, label="throughput (MiB/s)")
    ax.plot(x, moving_average(throughput, 5), color="#4C78A8", linestyle="--", linewidth=1.3, alpha=0.8, label="throughput MA(5)")
    ax.set_title("Host log: Throughput timeline")
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("MiB/s")
    ax2 = ax.twinx()
    ax2.plot(x, calls, color="#72B7B2", linewidth=1.5, alpha=0.8, label="calls/s")
    ax2.set_ylabel("calls/s")
    lines = ax.get_lines() + ax2.get_lines()
    ax.legend(lines, [line.get_label() for line in lines], loc="best", fontsize=8)

    ax = axes[0, 1]
    ax.plot(x, avg_us, color="black", linewidth=2.2, label="avg")
    ax.plot(x, queue_wait, color="#E45756", linewidth=2.0, label="queue_wait")
    ax.plot(x, notify, color="#F58518", linewidth=1.9, label="notify")
    ax.plot(x, complete, color="#54A24B", linewidth=1.5, label="complete")
    ax.plot(x, copy, color="#B279A2", linewidth=1.5, label="copy")
    ax.set_yscale("log")
    ax.set_title("Host log: Latency timeline (log-scale)")
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("Latency (us, log)")
    ax.legend(loc="best", fontsize=8)

    ax = axes[1, 0]
    ax.step(x, bottleneck_codes, where="mid", color="#4C78A8", linewidth=2.0)
    ax.scatter(x, bottleneck_codes, color="#E45756", s=28, zorder=3)
    ax.set_title("Host log: Dominant bottleneck over time")
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("Dominant component")
    ax.set_yticks(list(range(len(bottleneck_labels))))
    ax.set_yticklabels(bottleneck_labels)
    ax.grid(True, axis="y", alpha=0.35)

    ax = axes[1, 1]
    labels = [str(item["component"]) for item in pareto]
    median_values = [float(item["median_us"]) for item in pareto]
    cumulative = [float(item["cum_share_pct"]) for item in pareto]
    bars = ax.bar(labels, median_values, color="#4C78A8", alpha=0.85)
    ax.set_title("Host log: Pareto of median component latency")
    ax.set_xlabel("Component")
    ax.set_ylabel("Median latency (us)")
    ax.tick_params(axis="x", rotation=25)
    ax2 = ax.twinx()
    ax2.plot(labels, cumulative, color="#E45756", marker="o", linewidth=2.0)
    ax2.axhline(80.0, color="#E45756", linestyle="--", linewidth=1.2, alpha=0.7)
    ax2.set_ylabel("Cumulative share (%)")
    ax2.set_ylim(0, 105)
    if bars:
        bars[0].set_color("#F58518")
        bars[0].set_alpha(0.95)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output_png}")


def print_summary(rows: List[Dict[str, float | str]], pareto: List[Dict[str, float | str]]) -> None:
    avg_latency = median([float(row["avg_us"]) for row in rows]) if rows else 0.0
    avg_throughput = median([float(row["throughput_mib_s"]) for row in rows]) if rows else 0.0
    top_bottleneck = max(pareto, key=lambda item: float(item["median_us"])) if pareto else None
    queue_wait_dominant = sum(1 for row in rows if row["bottleneck_component"] == "queue_wait")

    print(f"host.log median avg latency: {avg_latency:.2f}us")
    print(f"host.log median throughput: {avg_throughput:.2f}MiB/s")
    print(f"host.log queue_wait-dominant seconds: {queue_wait_dominant}/{len(rows)}")
    if top_bottleneck:
        print(
            "host.log top bottleneck: "
            f"{top_bottleneck['component']} (median={float(top_bottleneck['median_us']):.2f}us, "
            f"share={float(top_bottleneck['share_pct']):.1f}%)"
        )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    host_log = repo_root / "results" / "host-log" / "before-optimization.log"

    if not host_log.exists():
        print("host log not found.")
        return

    rows = parse_host_log(host_log)
    if not rows:
        print(f"No valid WB-HOST perf lines parsed from: {host_log}")
        return

    pareto = make_pareto(rows)

    out_fig = repo_root / "output-figures" / "host_wb_bottleneck_dashboard.png"
    out_timeseries_csv = repo_root / "output-csv" / "host_wb_timeseries.csv"
    out_pareto_csv = repo_root / "output-csv" / "host_wb_component_pareto.csv"

    save_csv(
        rows,
        out_timeseries_csv,
        [
            "time_sec",
            "kernel_ts_sec",
            "elapsed_sec",
            "calls",
            "segs",
            "bytes",
            "throughput_mib_s",
            "avg_us",
            "queue_wait_us",
            "mcp_read_us",
            "parse_us",
            "copy_us",
            "notify_us",
            "complete_us",
            "mcp_ready",
            "parse_fail",
            "copy_done",
            "read_done",
            "fallback",
            "mode",
            "bottleneck_component",
            "bottleneck_value_us",
            "bottleneck_code",
            "queue_wait_share_pct",
            "notify_share_pct",
        ],
    )
    save_csv(
        pareto,
        out_pareto_csv,
        ["component", "median_us", "share_pct", "cum_share_pct"],
    )

    print(f"Saved: {out_timeseries_csv}")
    print(f"Saved: {out_pareto_csv}")
    plot_dashboard(rows, pareto, out_fig)
    print_summary(rows, pareto)


if __name__ == "__main__":
    main()