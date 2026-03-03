#!/usr/bin/env python3
"""
FIO L2P Cache Results Plotter (updated for NUM_JOBS × QD sweep)

更新点：
1) 支持新的测试结构：每个 cache size 下做 NUM_JOBS 扫描，再做 QD 扫描。
2) 数据源切换到仓库内 results/ 目录。
3) 自动从日志文件名识别 cache size（例如: L2Size512KB），不再依赖硬编码映射。
4) 绘图改为“每个 NUM_JOBS 一个子图”，横轴 QD，分别输出多种指标图。
"""

from __future__ import annotations

import re
import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


def parse_iops(iops_text: str) -> float:
    text = iops_text.strip()
    if text.endswith("k"):
        return float(text[:-1]) * 1000.0
    return float(text)


def parse_bw_to_mib(value_text: str, unit_text: str) -> float:
    value = float(value_text)
    unit = unit_text.strip()
    if unit == "KiB/s":
        return value / 1024.0
    if unit == "MiB/s":
        return value
    if unit == "GiB/s":
        return value * 1024.0
    return value


def convert_latency_to_us(value: float, unit: str, suffix: str = "") -> float:
    if suffix == "k":
        value *= 1000
    elif suffix == "m":
        value *= 1000000
    elif suffix == "u":
        value *= 1
    elif suffix == "n":
        value /= 1000

    if unit in ["nsec", "nsecs"]:
        return value / 1000.0
    if unit in ["msec", "msecs"]:
        return value * 1000.0
    return value


def extract_all_latency_metrics(section: str) -> Optional[Dict[str, float]]:
    clat_match = re.search(
        r"clat \(([^)]+)\):\s+min=([\d.]+)([kmun]*),\s+max=([\d.]+)([kmun]*),\s+avg=([\d.]+)([kmun]*),\s+stdev=([\d.]+)([kmun]*)",
        section,
    )
    if not clat_match:
        return None

    unit = clat_match.group(1)
    values = {
        "min": (float(clat_match.group(2)), clat_match.group(3)),
        "max": (float(clat_match.group(4)), clat_match.group(5)),
        "avg": (float(clat_match.group(6)), clat_match.group(7)),
        "stdev": (float(clat_match.group(8)), clat_match.group(9)),
    }

    metrics: Dict[str, float] = {}
    for key, (val, suffix) in values.items():
        metrics[key] = convert_latency_to_us(val, unit, suffix)
    return metrics


def extract_latency_percentiles(section: str) -> Optional[Dict[str, float]]:
    header = re.search(r"clat percentiles \(([^)]+)\):", section)
    if not header:
        return None

    pct_unit = header.group(1)
    targets = {
        "latency_p90": "90.00th",
        "latency_p99": "99.00th",
        "latency_p999": "99.90th",
    }

    metrics: Dict[str, float] = {}
    for key, label in targets.items():
        match = re.search(rf"{re.escape(label)}=\[\s*([\d.]+)([kmun]?)\]", section)
        if not match:
            return None
        value = float(match.group(1))
        suffix = match.group(2)
        metrics[key] = convert_latency_to_us(value, pct_unit, suffix)
    return metrics


def extract_cache_size_from_filename(log_path: Path) -> str:
    name = log_path.name
    m = re.search(r"L2Size([^-_.]+)", name, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    if "dram" in name.lower():
        return "DRAM"
    # fallback: 兼容旧命名
    return log_path.stem


def cache_sort_key(label: str) -> Tuple[int, str]:
    if label == "DRAM":
        return (10**12, label)
    m = re.match(r"(\d+)(KB|MB|GB)$", label, re.IGNORECASE)
    if not m:
        return (10**11, label)
    value = int(m.group(1))
    unit = m.group(2).upper()
    scale = {"KB": 1, "MB": 1024, "GB": 1024 * 1024}[unit]
    return (value * scale, label)


def latest_logs_by_cache(data_dir: Path) -> Dict[str, Path]:
    """同一 cache size 若有多份日志，仅保留文件名按字典序最大的（通常是最新时间戳）。"""
    best: Dict[str, Path] = {}
    for p in sorted(data_dir.glob("fio_test_*.log")):
        cache = extract_cache_size_from_filename(p)
        if cache not in best or p.name > best[cache].name:
            best[cache] = p
    return best


def parse_fio_log_with_numjobs(log_file: Path, op_type: str = "read") -> Dict[str, List[dict]]:
    content = log_file.read_text(encoding="utf-8", errors="ignore")
    points: List[dict] = []

    nj_iter = list(re.finditer(r"^========== Testing NUM_JOBS=(\d+) ==========$", content, re.MULTILINE))
    for idx, nj_match in enumerate(nj_iter):
        num_jobs = int(nj_match.group(1))
        start = nj_match.end()
        end = nj_iter[idx + 1].start() if idx + 1 < len(nj_iter) else len(content)
        nj_block = content[start:end]

        qd_sections = re.split(r"--- Testing QD=(\d+) ---", nj_block)
        for i in range(1, len(qd_sections), 2):
            if i + 1 >= len(qd_sections):
                continue
            qd = int(qd_sections[i])
            section = qd_sections[i + 1]

            iops_match = re.search(rf"{op_type}: IOPS=([\d.]+k?)", section)
            bw_match = re.search(r"BW=([\d.]+)(KiB/s|MiB/s|GiB/s)", section)
            latency_metrics = extract_all_latency_metrics(section)
            latency_percentiles = extract_latency_percentiles(section)

            if not iops_match or not bw_match or not latency_metrics or not latency_percentiles:
                continue

            points.append(
                {
                    "num_jobs": num_jobs,
                    "qd": qd,
                    "iops": parse_iops(iops_match.group(1)),
                    "bandwidth": parse_bw_to_mib(bw_match.group(1), bw_match.group(2)),
                    "latency_min": latency_metrics["min"],
                    "latency_max": latency_metrics["max"],
                    "latency_avg": latency_metrics["avg"],
                    "latency_p90": latency_percentiles["latency_p90"],
                    "latency_p99": latency_percentiles["latency_p99"],
                    "latency_p999": latency_percentiles["latency_p999"],
                }
            )

    return {"points": points}


def parse_dataset(data_dir: Path, op_type: str) -> Dict[str, Dict[str, List[dict]]]:
    all_data: Dict[str, Dict[str, List[dict]]] = {}
    for cache_size, log_file in sorted(latest_logs_by_cache(data_dir).items(), key=lambda x: cache_sort_key(x[0])):
        print(f"Parsing {op_type} {cache_size}: {log_file.name}")
        data = parse_fio_log_with_numjobs(log_file, op_type=op_type)
        if data["points"]:
            all_data[cache_size] = data
            print(f"  Found {len(data['points'])} points")
        else:
            print("  Warning: no valid points found")
    return all_data


def collect_numjobs(all_data: Dict[str, Dict[str, List[dict]]]) -> List[int]:
    numjobs = set()
    for cache_data in all_data.values():
        for p in cache_data["points"]:
            numjobs.add(p["num_jobs"])
    return sorted(numjobs)


def extract_qd_metric_for_numjobs(points: List[dict], target_numjobs: int, metric_key: str) -> Tuple[List[int], List[float]]:
    subset = [p for p in points if p["num_jobs"] == target_numjobs]
    subset.sort(key=lambda x: x["qd"])
    qd = [p["qd"] for p in subset]
    values = [p[metric_key] for p in subset]
    if metric_key == "iops":
        values = [v / 1000.0 for v in values]
    return qd, values


def plot_numjobs_panel(
    ax,
    all_data: Dict[str, Dict[str, List[dict]]],
    cache_sizes: List[str],
    colors: List[str],
    target_numjobs: int,
    metric_key: str,
    ylabel: str,
    title: str,
) -> None:
    for idx, cache_size in enumerate(cache_sizes):
        if cache_size not in all_data:
            continue
        qd, y = extract_qd_metric_for_numjobs(all_data[cache_size]["points"], target_numjobs, metric_key)
        if not qd:
            continue
        style = "--" if cache_size == "DRAM" else "-"
        ax.plot(qd, y, color=colors[idx % len(colors)], label=cache_size, linewidth=2, linestyle=style)

    ax.set_ylabel(ylabel, fontsize=10)
    ax.set_xlabel("Queue Depth (QD)", fontsize=10)
    ax.set_title(title, fontsize=11)
    ax.grid(True, alpha=0.3)


def generate_metric_figure(
    metric_key: str,
    ylabel: str,
    fig_title: str,
    output_path: Path,
    read_data: Dict[str, Dict[str, List[dict]]],
    write_data: Dict[str, Dict[str, List[dict]]],
    cache_sizes: List[str],
    colors: List[str],
    all_numjobs: List[int],
) -> None:
    fig, axes = plt.subplots(len(all_numjobs), 2, figsize=(9, 3.2 * len(all_numjobs)), squeeze=False)
    fig.suptitle(fig_title, fontsize=16, fontweight="bold", y=0.995)
    fig.text(0.5, 0.975, "Data source: results/fio-l2p-cache-randread-4k-1G + randwrite-4k-1G", ha="center", va="top", fontsize=9)
    fig.text(0.5, 0.963, "fio-3.28, ioengine=libaio, size=1G, bs=4k, runtime=3s", ha="center", va="top", fontsize=9)
    fig.text(0.5, 0.951, "CPU: Intel Core i7-14700KF@5.6GHz, RAM: 128GB", ha="center", va="top", fontsize=9)

    for row, nj in enumerate(all_numjobs):
        plot_numjobs_panel(
            axes[row, 0],
            read_data,
            cache_sizes,
            colors,
            nj,
            metric_key,
            ylabel,
            f"Randread: NUM_JOBS={nj}",
        )
        plot_numjobs_panel(
            axes[row, 1],
            write_data,
            cache_sizes,
            colors,
            nj,
            metric_key,
            ylabel,
            f"Randwrite: NUM_JOBS={nj}",
        )

    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        fig.legend(
            handles,
            labels,
            loc="upper center",
            ncol=min(6, len(labels)),
            fontsize=9,
            frameon=False,
            bbox_to_anchor=(0.5, 0.942),
        )

    plt.tight_layout(rect=[0, 0, 1, 0.92])
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output_path}")


def build_clean_plot_rows(
    metric_key: str,
    read_data: Dict[str, Dict[str, List[dict]]],
    write_data: Dict[str, Dict[str, List[dict]]],
    cache_sizes: List[str],
    all_numjobs: List[int],
) -> List[dict]:
    """
    Build cleaned rows used by plotting:
    - keep only rows that can actually be drawn (matching metric/numjobs/cache)
    - normalize iops to K (same as figure)
    - deduplicate by (rw, num_jobs, cache_size, qd)
    - sort by rw, num_jobs, cache_size, qd
    """
    rows: List[dict] = []

    def collect_rows(rw_label: str, dataset: Dict[str, Dict[str, List[dict]]]) -> None:
        for num_jobs in all_numjobs:
            for cache_size in cache_sizes:
                if cache_size not in dataset:
                    continue
                qd_vals, y_vals = extract_qd_metric_for_numjobs(
                    dataset[cache_size]["points"],
                    num_jobs,
                    metric_key,
                )
                for qd, y in zip(qd_vals, y_vals):
                    if y is None:
                        continue
                    rows.append(
                        {
                            "rw": rw_label,
                            "num_jobs": int(num_jobs),
                            "cache_size": cache_size,
                            "qd": int(qd),
                            "value": float(y),
                        }
                    )

    collect_rows("randread", read_data)
    collect_rows("randwrite", write_data)

    dedup: Dict[Tuple[str, int, str, int], dict] = {}
    for r in rows:
        key = (r["rw"], r["num_jobs"], r["cache_size"], r["qd"])
        dedup[key] = r

    cleaned = list(dedup.values())
    cleaned.sort(key=lambda r: (r["rw"], r["num_jobs"], cache_sort_key(r["cache_size"]), r["qd"]))
    return cleaned


def save_metric_csv(
    csv_path: Path,
    metric_key: str,
    metric_unit: str,
    read_data: Dict[str, Dict[str, List[dict]]],
    write_data: Dict[str, Dict[str, List[dict]]],
    cache_sizes: List[str],
    all_numjobs: List[int],
) -> None:
    rows = build_clean_plot_rows(
        metric_key=metric_key,
        read_data=read_data,
        write_data=write_data,
        cache_sizes=cache_sizes,
        all_numjobs=all_numjobs,
    )

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["rw", "num_jobs", "cache_size", "qd", "metric", "unit", "value"])
        for r in rows:
            writer.writerow([
                r["rw"],
                r["num_jobs"],
                r["cache_size"],
                r["qd"],
                metric_key,
                metric_unit,
                f"{r['value']:.6f}",
            ])

    print(f"Saved: {csv_path}")


def print_summary(op_name: str, dataset: Dict[str, Dict[str, List[dict]]], cache_sizes: List[str]) -> None:
    print(f"\n{op_name} summary (best points over NUM_JOBS×QD):")
    for cache in cache_sizes:
        if cache not in dataset:
            continue
        points = dataset[cache]["points"]
        best_bw = max(points, key=lambda x: x["bandwidth"])
        best_iops = max(points, key=lambda x: x["iops"])
        best_lat = min(points, key=lambda x: x["latency_avg"])
        print(f"  [{cache}]")
        print(
            f"    Max BW:   {best_bw['bandwidth']:.2f} MiB/s "
            f"(NUM_JOBS={best_bw['num_jobs']}, QD={best_bw['qd']})"
        )
        print(
            f"    Max IOPS: {best_iops['iops']:.0f} "
            f"(NUM_JOBS={best_iops['num_jobs']}, QD={best_iops['qd']})"
        )
        print(
            f"    Min Avg Lat: {best_lat['latency_avg']:.2f} us "
            f"(NUM_JOBS={best_lat['num_jobs']}, QD={best_lat['qd']})"
        )


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    results_root = repo_root / "results"
    read_dir = results_root / "fio-l2p-cache-randread-4k-1G"
    write_dir = results_root / "fio-l2p-cache-randwrite-4k-1G"

    read_data = parse_dataset(read_dir, op_type="read")
    write_data = parse_dataset(write_dir, op_type="write")

    cache_sizes = sorted(set(read_data.keys()) | set(write_data.keys()), key=cache_sort_key)
    if not cache_sizes:
        print("No parsed data found. Please check results directory and log format.")
        return

    colors = ["#9bbd5b", "#e4da51", "#eea460", "#e07288", "#8e73f0", "#4c78a8", "#f58518"]

    all_numjobs = sorted(set(collect_numjobs(read_data)) | set(collect_numjobs(write_data)))
    if not all_numjobs:
        print("No NUM_JOBS sections found in parsed data.")
        return

    output_fig_dir = repo_root / "output-figures"
    output_csv_dir = repo_root / "output-csv"
    output_fig_dir.mkdir(parents=True, exist_ok=True)
    output_csv_dir.mkdir(parents=True, exist_ok=True)

    figure_specs = [
        ("bandwidth", "Bandwidth (MiB/s)", "MiB/s", "FIO L2P Cache - Bandwidth (Per-NUM_JOBS)", "fio_l2p_cache_bandwidth_plot.png"),
        ("iops", "IOPS (K)", "KIOPS", "FIO L2P Cache - IOPS (Per-NUM_JOBS)", "fio_l2p_cache_iops_plot.png"),
        ("latency_p90", "Latency (us)", "us", "FIO L2P Cache - P90 Latency (Per-NUM_JOBS)", "fio_l2p_cache_p90lat_plot.png"),
        ("latency_p99", "Latency (us)", "us", "FIO L2P Cache - P99 Latency (Per-NUM_JOBS)", "fio_l2p_cache_p99lat_plot.png"),
        ("latency_avg", "Latency (us)", "us", "FIO L2P Cache - Avg Latency (Per-NUM_JOBS)", "fio_l2p_cache_avglat_plot.png"),
    ]

    print("\nGenerating figures...")
    for metric_key, ylabel, metric_unit, title, filename in figure_specs:
        png_path = output_fig_dir / filename
        generate_metric_figure(
            metric_key=metric_key,
            ylabel=ylabel,
            fig_title=title,
            output_path=png_path,
            read_data=read_data,
            write_data=write_data,
            cache_sizes=cache_sizes,
            colors=colors,
            all_numjobs=all_numjobs,
        )
        csv_path = output_csv_dir / f"{Path(filename).stem}.csv"
        save_metric_csv(
            csv_path=csv_path,
            metric_key=metric_key,
            metric_unit=metric_unit,
            read_data=read_data,
            write_data=write_data,
            cache_sizes=cache_sizes,
            all_numjobs=all_numjobs,
        )

    print("\n" + "=" * 80)
    print("Performance Summary")
    print("=" * 80)
    print_summary("Randread", read_data, cache_sizes)
    print_summary("Randwrite", write_data, cache_sizes)

    print(f"\nAll figures are saved under: {output_fig_dir}")
    print(f"All CSV files are saved under: {output_csv_dir}")


if __name__ == "__main__":
    main()
