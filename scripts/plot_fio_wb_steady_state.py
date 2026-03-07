#!/usr/bin/env python3
"""
根据 fio --write_iops_log 输出绘制稳态散点图。

数据来源：results/wb_steady_state/
默认匹配：*_iops.log_iops.*.log*

输出：
- output-figures/fio_wb_steady_state_scatter.png
- output-csv/fio_wb_steady_state_scatter.csv
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt


@dataclass
class SteadyStateSeries:
    scenario: str
    source_name: str
    time_sec: List[float]
    iops: List[float]


def infer_scenario_name(path: Path) -> str:
    name = path.name
    if ".log-" in name:
        return name.split(".log-", 1)[1]
    if "-" in name:
        return name.split("-")[-1]
    return path.stem


def infer_scenario_name_from_group_key(group_key: str) -> str:
    # 优先使用类似 *.log-WB_Disabled 这种后缀作为场景名
    if ".log-" in group_key:
        return group_key.split(".log-", 1)[1]
    marker = "_iops.log_iops-"
    if marker in group_key:
        return group_key.split(marker, 1)[1]
    # 无后缀时，使用测试前缀
    if "_iops.log_iops" in group_key:
        return group_key.split("_iops.log_iops")[0]
    return group_key


def split_sharded_iops_log_name(name: str) -> Tuple[str, int] | None:
    """
    识别 fio iops shard 文件名：
    - ssd_test_xxx_iops.log_iops.1.log
    - ssd_test_xxx_iops.log_iops.1.log-WB_Disabled
    返回：("公共前缀+尾部标签", shard_index)
    """
    m = re.match(r"^(.*_iops\.log_iops)\.(\d+)\.log(.*)$", name)
    if not m:
        return None
    prefix = m.group(1)
    shard_index = int(m.group(2))
    tail = m.group(3)
    group_key = f"{prefix}{tail}"
    return group_key, shard_index


def read_iops_points(path: Path) -> Tuple[List[float], List[float]]:
    t: List[float] = []
    y: List[float] = []

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                t_ms = float(parts[0])
                iops = float(parts[1])
            except ValueError:
                continue
            t.append(t_ms / 1000.0)
            y.append(iops)

    return t, y


def parse_iops_log(path: Path) -> SteadyStateSeries:
    t, y = read_iops_points(path)

    return SteadyStateSeries(
        scenario=infer_scenario_name(path),
        source_name=path.name,
        time_sec=t,
        iops=y,
    )


def merge_sharded_iops_logs(group_key: str, files: List[Path]) -> SteadyStateSeries:
    parsed = [read_iops_points(p) for p in files]
    parsed = [(t, y) for t, y in parsed if t and y]
    if not parsed:
        return SteadyStateSeries(
            scenario=infer_scenario_name_from_group_key(group_key),
            source_name=f"{group_key} (merged {len(files)} logs)",
            time_sec=[],
            iops=[],
        )

    base_t, base_y = parsed[0]
    sum_iops = list(base_y)

    for t, y in parsed[1:]:
        n = min(len(base_t), len(sum_iops), len(t), len(y))
        if n <= 0:
            base_t = []
            sum_iops = []
            break

        # 采样点如果长度不一致，按最短长度对齐
        if len(t) != len(base_t):
            print(f"Warning: shard length mismatch for {group_key}, aligned to {n} points")

        # 时间轴若有微小偏差，按索引对齐（用户数据采样一致）
        for i in range(n):
            sum_iops[i] += y[i]

        base_t = base_t[:n]
        sum_iops = sum_iops[:n]

    return SteadyStateSeries(
        scenario=infer_scenario_name_from_group_key(group_key),
        source_name=f"{group_key} (sum of {len(files)} shards)",
        time_sec=base_t,
        iops=sum_iops,
    )


def build_series_from_files(log_files: List[Path]) -> List[SteadyStateSeries]:
    grouped: Dict[str, List[Tuple[int, Path]]] = {}

    for p in log_files:
        split = split_sharded_iops_log_name(p.name)
        if split is None:
            grouped.setdefault(p.name, []).append((1, p))
            continue
        group_key, shard_idx = split
        grouped.setdefault(group_key, []).append((shard_idx, p))

    out: List[SteadyStateSeries] = []
    for group_key in sorted(grouped.keys()):
        shard_items = sorted(grouped[group_key], key=lambda x: x[0])
        files = [p for _, p in shard_items]
        if len(files) == 1:
            out.append(parse_iops_log(files[0]))
        else:
            out.append(merge_sharded_iops_logs(group_key, files))

    return out


def moving_average(values: List[float], window: int = 9) -> List[float]:
    if not values:
        return []
    w = max(1, window)
    out: List[float] = []
    acc = 0.0
    q: List[float] = []
    for v in values:
        q.append(v)
        acc += v
        if len(q) > w:
            acc -= q.pop(0)
        out.append(acc / len(q))
    return out


def steady_tail_slice(n: int, tail_ratio: float = 0.30, min_points: int = 20) -> slice:
    if n <= 0:
        return slice(0, 0)
    tail_n = max(min_points, int(n * tail_ratio))
    tail_n = min(n, tail_n)
    return slice(n - tail_n, n)


def summarize_tail(values: List[float], tail: slice) -> Tuple[float, float, float]:
    tail_vals = values[tail]
    if not tail_vals:
        return 0.0, 0.0, 0.0
    vals = sorted(tail_vals)
    p10 = vals[max(0, int(0.10 * (len(vals) - 1)))]
    p90 = vals[max(0, int(0.90 * (len(vals) - 1)))]
    med = median(vals)
    return p10, med, p90


def save_csv(rows: List[Tuple[str, float, float, int]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["scenario", "time_sec", "iops", "in_steady_tail"])
        for r in rows:
            writer.writerow([r[0], f"{r[1]:.3f}", f"{r[2]:.3f}", r[3]])


def plot_steady_state_scatter(series_list: List[SteadyStateSeries], output_png: Path) -> None:
    if not series_list:
        print("No wb steady-state iops logs found.")
        return

    fig_h = max(4.0, 3.6 * len(series_list))
    fig, axes = plt.subplots(len(series_list), 1, figsize=(11, fig_h), squeeze=False)
    fig.suptitle("FIO Write Buffer Steady-State Scatter", fontsize=16, fontweight="bold", y=0.995)
    # fig.text(0.5, 0.968, "Data source: results/wb_steady_state/*_iops.log_iops.*.log*", ha="center", va="top", fontsize=9)

    colors = [
        "#54A24B",
        "#B279A2",
        "#E45756",
        "#4C78A8",
        "#F58518",
    ]

    for idx, s in enumerate(series_list):
        ax = axes[idx, 0]
        c = colors[idx % len(colors)]

        tail = steady_tail_slice(len(s.iops), tail_ratio=0.30, min_points=20)
        p10, med, p90 = summarize_tail(s.iops, tail)
        iops_ma = moving_average(s.iops, window=9)

        ax.scatter(s.time_sec, s.iops, s=16, alpha=0.65, color=c, label=f"{s.scenario} points")
        ax.plot(s.time_sec, iops_ma, color="black", linewidth=1.6, alpha=0.9, label="moving avg (window=9)")

        if tail.start is not None and tail.start < len(s.time_sec):
            steady_begin_t = s.time_sec[tail.start]
            steady_end_t = s.time_sec[-1]
            ax.axvspan(steady_begin_t, steady_end_t, color="#d9d9d9", alpha=0.25, label="steady-tail window")

        if med > 0:
            ax.axhline(med, color="#2ca02c", linestyle="--", linewidth=1.5, label=f"tail median={med:.0f}")
            ax.axhline(p10, color="#2ca02c", linestyle=":", linewidth=1.2, alpha=0.8)
            ax.axhline(p90, color="#2ca02c", linestyle=":", linewidth=1.2, alpha=0.8)

        if s.scenario.startswith("Job"):
            ax.set_title(s.scenario, fontsize=11)
        else:
            ax.set_title(f"{s.scenario}  ({s.source_name})", fontsize=11)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("IOPS")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

        print(
            f"[{s.scenario}] points={len(s.iops)}, "
            f"tail-median={med:.0f}, tail-p10={p10:.0f}, tail-p90={p90:.0f}"
        )

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output_png}")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = repo_root / "results" / "wb_steady_state"

    log_files = sorted(data_dir.glob("*_iops.log_iops.*.log*"))
    if not log_files:
        print(f"No matched files under: {data_dir}")
        return

    series_list = build_series_from_files(log_files)
    series_list = [s for s in series_list if s.time_sec and s.iops]
    if not series_list:
        print("No valid datapoints parsed from iops logs.")
        return

    output_png = repo_root / "output-figures" / "fio_wb_steady_state_scatter.png"
    output_csv = repo_root / "output-csv" / "fio_wb_steady_state_scatter.csv"

    rows: List[Tuple[str, float, float, int]] = []
    for s in series_list:
        tail = steady_tail_slice(len(s.iops), tail_ratio=0.30, min_points=20)
        for i, (t, y) in enumerate(zip(s.time_sec, s.iops)):
            in_tail = 1 if (tail.start is not None and i >= tail.start) else 0
            rows.append((s.scenario, t, y, in_tail))

    save_csv(rows, output_csv)
    print(f"Saved: {output_csv}")

    plot_steady_state_scatter(series_list, output_png)


if __name__ == "__main__":
    main()
