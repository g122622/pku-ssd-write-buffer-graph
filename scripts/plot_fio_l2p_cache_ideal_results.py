#!/usr/bin/env python3
"""
FIO L2P Cache Theoretical Results Plotter

风格对齐 `plot_fio_l2p_cache_results.py`：
1) 按 NUM_JOBS 分行、读写分列。
2) 横轴 QD 取值直接对齐实验脚本。
3) 输出理论 IOPS / Avg Lat 图与对应 CSV。

修正后的理论模型分两层：
1) FEMU 设备侧模型：保留 BBSSD + multilevel-L2P 的 closed-network MVA；
2) Host-visible 修正：加入此前实验和日志中确认的 guest front-end 瓶颈。

它不再把结果解释为“纯 device-side upper bound”，而是更接近
guest 内 fio 实际可见的 performance envelope：
- READ: 设备时延稳定，但会被 per-job issue/reap 与 guest 前端吞吐上限截断；
- WRITE: 在前述基础上，再叠加一个与 QD/L2 容量相关的经验 GC 惩罚项，
    用于近似实验里观察到的高 QD 下写性能回落。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt


READ_QD_SWEEP = [1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32]
WRITE_QD_SWEEP = [
    1,
    2,
    4,
    8,
    16,
    24,
    32,
    40,
    48,
    56,
    64,
    72,
    80,
    88,
    96,
    104,
    112,
    120,
    128,
]
NUM_JOBS_SWEEP = [1, 2, 4, 8, 16, 32, 64]
L1_SIZE_KB = 256
L2_CACHE_SIZES_KB = [512, 1024, 1536, 2048]
COLORS = ["#9bbd5b", "#e4da51", "#eea460", "#e07288", "#8e73f0", "#4c78a8", "#f58518"]

VM_VCPUS = 16

READ_HOST_FIXED_US = 7.8
WRITE_HOST_FIXED_US = 7.0

READ_FRONTEND_BASE_IOPS = 118_000.0
READ_FRONTEND_NUMJOBS_ALPHA = 0.44
READ_FRONTEND_VCPU_ALPHA = 0.90
READ_FRONTEND_QD_KNEE = 5.0

WRITE_FRONTEND_BASE_BY_L2_KB = {
    512: 70_000.0,
    1024: 78_000.0,
    1536: 86_000.0,
    2048: 94_000.0,
}
WRITE_FRONTEND_NUMJOBS_ALPHA = 0.22
WRITE_FRONTEND_VCPU_ALPHA = 0.35
WRITE_FRONTEND_QD_KNEE = 18.0
WRITE_GC_QD_KNEE_BASE = 44.0
WRITE_GC_QD_KNEE_PER_KB = 1.0 / 24.0
WRITE_GC_STRENGTH_BASE = 0.28
WRITE_GC_STRENGTH_EXP = 0.65
WRITE_GC_PRESSURE_EXP = 1.2

DEFAULT_NOISE_RATIO = 0.03
DEFAULT_NOISE_SEED = 20260312
DEFAULT_NOISE_MODE = "uniform"


@dataclass(frozen=True)
class NoiseConfig:
    ratio: float = DEFAULT_NOISE_RATIO
    seed: int = DEFAULT_NOISE_SEED
    mode: str = DEFAULT_NOISE_MODE

    @property
    def enabled(self) -> bool:
        return self.ratio > 0.0


def calc_iops(num_jobs, qd, l1_size, l2_size, is_write):
    return _calc_model(num_jobs, qd, l1_size, l2_size, is_write)[0]


def calc_avg_lat(num_jobs, qd, l1_size, l2_size, is_write):
    return _calc_model(num_jobs, qd, l1_size, l2_size, is_write)[1]


def _calc_model(num_jobs, qd, l1_size, l2_size, is_write):
    device_iops, device_lat_us = _calc_device_model(
        num_jobs=num_jobs,
        qd=qd,
        l1_size=l1_size,
        l2_size=l2_size,
        is_write=is_write,
    )

    K = int(num_jobs) * int(qd)
    host_fixed_us = WRITE_HOST_FIXED_US if is_write else READ_HOST_FIXED_US
    visible_floor_lat_us = device_lat_us + host_fixed_us
    visible_floor_iops = K * 1_000_000.0 / visible_floor_lat_us

    if is_write:
        frontend_cap_iops = _calc_write_frontend_cap_iops(num_jobs, qd, l2_size)
    else:
        frontend_cap_iops = _calc_read_frontend_cap_iops(num_jobs, qd)

    corrected_iops = min(device_iops, visible_floor_iops, frontend_cap_iops)
    corrected_lat_us = K * 1_000_000.0 / corrected_iops
    corrected_lat_us = max(corrected_lat_us, visible_floor_lat_us)
    return corrected_iops, corrected_lat_us


def _build_point_rng(
    noise_config: NoiseConfig,
    *,
    is_write: bool,
    num_jobs: int,
    qd: int,
    l1_size: int,
    l2_size: int,
) -> random.Random:
    point_key = (
        f"seed={noise_config.seed}|rw={'write' if is_write else 'read'}|"
        f"nj={num_jobs}|qd={qd}|l1={l1_size}|l2={l2_size}"
    )
    digest = hashlib.sha256(point_key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _sample_noise_factor(rng: random.Random, noise_config: NoiseConfig) -> float:
    if not noise_config.enabled:
        return 1.0

    if noise_config.mode == "gaussian":
        sigma = noise_config.ratio / 2.0
        jitter = rng.gauss(0.0, sigma)
    else:
        jitter = rng.uniform(-noise_config.ratio, noise_config.ratio)

    factor = 1.0 + jitter
    min_factor = max(0.05, 1.0 - noise_config.ratio)
    max_factor = 1.0 + noise_config.ratio
    return min(max(factor, min_factor), max_factor)


def _apply_noise_to_metrics(
    iops: float,
    avg_lat_us: float,
    *,
    noise_config: NoiseConfig,
    is_write: bool,
    num_jobs: int,
    qd: int,
    l1_size: int,
    l2_size: int,
) -> Tuple[float, float]:
    if not noise_config.enabled:
        return iops, avg_lat_us

    rng = _build_point_rng(
        noise_config,
        is_write=is_write,
        num_jobs=num_jobs,
        qd=qd,
        l1_size=l1_size,
        l2_size=l2_size,
    )
    performance_factor = _sample_noise_factor(rng, noise_config)
    latency_residual = rng.uniform(-noise_config.ratio / 3.0, noise_config.ratio / 3.0)

    adjusted_iops = max(1.0, iops * performance_factor)
    adjusted_lat_us = max(
        1e-6,
        avg_lat_us / performance_factor * (1.0 + latency_residual),
    )
    return adjusted_iops, adjusted_lat_us


def _calc_device_model(num_jobs, qd, l1_size, l2_size, is_write):
    """
    FEMU BBSSD device-side model for the given fixed configuration:

    - secsz = 512
    - secs_per_pg = 8           => data page size = 4 KiB
    - luns_per_ch = 8
    - nchs = 8                  => 64 LUNs
    - pg_rd_lat = 40000 ns      => 40 us
    - pg_wr_lat = 200000 ns     => 200 us
    - l2p_pt_page_size = 4096   => one PT page maps 512 LPNs => 2 MiB data
    - l2p_l1_rd_lat_ns = 0
    - l2p_l1_wr_lat_ns = 0
    - l2p_l2_rd_lat_ns = 300
    - l2p_l2_wr_lat_ns = 400
    - l2p_l3_rd_lat_mul = 1     => 40 us
    - l2p_l3_wr_lat_mul = 1     => 200 us
    - workload region = 1 GiB random access
    - WB disabled
    - multilevel L2P enabled
    - no external dependencies

    Parameters:
        num_jobs: int
        qd: int
        l1_size: KiB
        l2_size: KiB
        is_write: bool

    Returns:
        (iops, avg_lat_us)

        Notes:
        - This helper only describes the device-side service model.
        - Host-visible corrections are added by `_calc_model()` on top of it.
    """
    if num_jobs <= 0 or qd <= 0:
        raise ValueError("num_jobs and qd must be positive")
    if l1_size < 0 or l2_size < 0:
        raise ValueError("l1_size and l2_size must be non-negative")

    K = int(num_jobs) * int(qd)  # total outstanding I/O in steady state

    # ---- Fixed configuration from your script / FEMU options ----
    DATA_PAGE_SIZE_KB = 4
    PT_PAGE_SIZE_KB = 4
    DATA_LUNS = 64
    META_LUNS = 64

    NAND_READ_US = 40.0
    NAND_WRITE_US = 200.0
    L2_RD_US = 0.3
    L2_WR_US = 0.4
    L3_RD_US = 40.0
    L3_WR_US = 200.0

    WORKING_SET_GIB = 1
    # One PT page covers 512 LPNs * 4 KiB = 2 MiB data.
    TOTAL_PT_PAGES = int(WORKING_SET_GIB * 1024 / 2)  # 1 GiB => 512 PT pages

    # ---- Cache coverage / hit probabilities under uniform random access ----
    l1_pt_pages = min(l1_size // PT_PAGE_SIZE_KB, TOTAL_PT_PAGES)
    l2_pt_pages = min(l2_size // PT_PAGE_SIZE_KB, TOTAL_PT_PAGES)

    p_l1 = l1_pt_pages / TOTAL_PT_PAGES
    p_l2_only = max(l2_pt_pages - l1_pt_pages, 0) / TOTAL_PT_PAGES
    p_miss = max(TOTAL_PT_PAGES - l2_pt_pages, 0) / TOTAL_PT_PAGES

    # ---- Non-queued fixed latency part (us) ----
    # Read-side metadata lookup path:
    # - L1 hit: 0
    # - L2 hit: +0.3us
    # - L3 miss: +0.3us (L2 read probe) +0.4us (install into L2)
    #
    # The L1 write/install latency is 0us in your config.
    fixed_lookup_us = p_l2_only * L2_RD_US + p_miss * (L2_RD_US + L2_WR_US)

    # ---- Queued service demand (us/request) ----
    if not is_write:
        # Read:
        # - data path: one NAND read => 40us on one of 64 data LUNs
        # - metadata path: only L3 misses consume metadata-LUN service => p_miss * 40us
        think_us = fixed_lookup_us
        demand_data_us = NAND_READ_US
        demand_meta_us = p_miss * L3_RD_US
    else:
        # Write:
        # 1) lookup old mapping: same as read lookup
        # 2) update mapping:
        #    - always does one L3 metadata write => 200us
        #    - if L2 slot miss, does an extra L3 metadata read before that => +40us * p_miss
        # Local L2/L1 updates are dominated by L3 op in the code's MAX() latency accounting.
        think_us = fixed_lookup_us
        demand_data_us = NAND_WRITE_US
        demand_meta_us = L3_WR_US + p_miss * L3_RD_US

    iops_per_us, avg_lat_us = _closed_network_mva(
        population=K,
        think_us=think_us,
        service_demands_us=(demand_data_us, demand_meta_us),
        servers=(DATA_LUNS, META_LUNS),
    )

    return iops_per_us * 1_000_000.0, avg_lat_us


def _calc_read_frontend_cap_iops(num_jobs: int, qd: int) -> float:
    asymptotic_cap = (
        READ_FRONTEND_BASE_IOPS
        * (float(num_jobs) ** READ_FRONTEND_NUMJOBS_ALPHA)
        * ((VM_VCPUS / 16.0) ** READ_FRONTEND_VCPU_ALPHA)
    )
    qd_ramp = 1.0 - math.exp(-float(qd) / READ_FRONTEND_QD_KNEE)
    return max(1.0, asymptotic_cap * qd_ramp)


def _calc_write_frontend_cap_iops(num_jobs: int, qd: int, l2_size: int) -> float:
    base_cap = WRITE_FRONTEND_BASE_BY_L2_KB.get(l2_size, 70_000.0)
    qd_ramp = 1.0 - math.exp(-float(qd) / WRITE_FRONTEND_QD_KNEE)
    job_penalty = float(num_jobs) ** WRITE_FRONTEND_NUMJOBS_ALPHA
    vcpu_scale = (VM_VCPUS / 16.0) ** WRITE_FRONTEND_VCPU_ALPHA

    qd_gc_knee = WRITE_GC_QD_KNEE_BASE + l2_size * WRITE_GC_QD_KNEE_PER_KB
    pressure = max(float(qd) - qd_gc_knee, 0.0) / qd_gc_knee
    gc_strength = WRITE_GC_STRENGTH_BASE * (
        (2048.0 / float(l2_size)) ** WRITE_GC_STRENGTH_EXP
    )
    gc_penalty = 1.0 + gc_strength * (pressure**WRITE_GC_PRESSURE_EXP)

    return max(1.0, base_cap * qd_ramp * vcpu_scale / (job_penalty * gc_penalty))


def _closed_network_mva(population, think_us, service_demands_us, servers):
    """
    Approximate multi-server Mean Value Analysis (MVA) for a closed queueing network.

    Centers:
    - delay center: fixed non-queued latency `think_us`
    - queueing centers: each with demand D_i and m_i parallel servers

    For single outstanding I/O this is exact.
    For larger concurrency it gives a smooth and usually very accurate FEMU-side
    theoretical curve for this workload.
    """
    qlens = [0.0 for _ in service_demands_us]
    throughput_per_us = 0.0
    response_us = 0.0

    for n in range(1, population + 1):
        residence_us = []
        for D, m, Q in zip(service_demands_us, servers, qlens):
            if D <= 0.0:
                residence_us.append(0.0)
            else:
                residence_us.append(D * (1.0 + Q / float(m)))

        response_us = think_us + sum(residence_us)
        throughput_per_us = n / response_us
        qlens = [throughput_per_us * r for r in residence_us]

    return throughput_per_us, response_us


def cache_sort_key(label: str) -> Tuple[int, str]:
    m = re.match(r"(\d+)(KB|MB|GB)$", label, re.IGNORECASE)
    if not m:
        return (10**11, label)
    value = int(m.group(1))
    unit = m.group(2).upper()
    scale = {"KB": 1, "MB": 1024, "GB": 1024 * 1024}[unit]
    return (value * scale, label)


def build_theoretical_dataset(
    is_write: bool, noise_config: NoiseConfig
) -> Dict[str, Dict[str, List[dict]]]:
    qd_sweep = WRITE_QD_SWEEP if is_write else READ_QD_SWEEP
    dataset: Dict[str, Dict[str, List[dict]]] = {}

    for l2_size_kb in L2_CACHE_SIZES_KB:
        cache_label = f"{l2_size_kb}KB"
        points: List[dict] = []
        for num_jobs in NUM_JOBS_SWEEP:
            for qd in qd_sweep:
                iops, avg_lat_us = _calc_model(
                    num_jobs=num_jobs,
                    qd=qd,
                    l1_size=L1_SIZE_KB,
                    l2_size=l2_size_kb,
                    is_write=is_write,
                )
                iops, avg_lat_us = _apply_noise_to_metrics(
                    iops,
                    avg_lat_us,
                    noise_config=noise_config,
                    is_write=is_write,
                    num_jobs=num_jobs,
                    qd=qd,
                    l1_size=L1_SIZE_KB,
                    l2_size=l2_size_kb,
                )
                points.append(
                    {
                        "num_jobs": num_jobs,
                        "qd": qd,
                        "iops": iops,
                        "latency_avg": avg_lat_us,
                    }
                )
        dataset[cache_label] = {"points": points}

    return dataset


def extract_qd_metric_for_numjobs(
    points: List[dict], target_numjobs: int, metric_key: str
) -> Tuple[List[int], List[float]]:
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
        qd, y = extract_qd_metric_for_numjobs(
            all_data[cache_size]["points"], target_numjobs, metric_key
        )
        if not qd:
            continue
        ax.plot(qd, y, color=colors[idx % len(colors)], label=cache_size, linewidth=2)

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
    noise_config: NoiseConfig,
) -> None:
    fig, axes = plt.subplots(
        len(all_numjobs), 2, figsize=(9, 3.2 * len(all_numjobs)), squeeze=False
    )
    fig.suptitle(fig_title, fontsize=16, fontweight="bold", y=0.995)
    fig.text(
        0.5,
        0.975,
        "Corrected model: FEMU device MVA + guest front-end bottleneck correction",
        ha="center",
        va="top",
        fontsize=9,
    )
    fig.text(
        0.5,
        0.963,
        f"L1 fixed to {L1_SIZE_KB}KB; L2 scan follows experiment; assumed VM vCPU={VM_VCPUS}",
        ha="center",
        va="top",
        fontsize=9,
    )
    fig.text(
        0.5,
        0.951,
        "Read uses per-job/front-end cap; Write adds heuristic GC/front-end penalty; bs=4k size=1G WB disabled",
        ha="center",
        va="top",
        fontsize=9,
    )
    fig.text(
        0.5,
        0.939,
        (
            "Random perturbation: "
            f"{'disabled' if not noise_config.enabled else f'{noise_config.mode} ±{noise_config.ratio:.1%}, seed={noise_config.seed}'}"
        ),
        ha="center",
        va="top",
        fontsize=9,
    )

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

    plt.tight_layout(rect=[0, 0, 1, 0.91])
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot corrected theoretical L2P cache results with optional random perturbation."
    )
    parser.add_argument(
        "--noise-ratio",
        type=float,
        default=DEFAULT_NOISE_RATIO,
        help=(
            "Relative random perturbation strength for each point. "
            "0 disables noise, 0.03 means about ±3%%."
        ),
    )
    parser.add_argument(
        "--noise-seed",
        type=int,
        default=DEFAULT_NOISE_SEED,
        help="Seed used to generate reproducible per-point perturbations.",
    )
    parser.add_argument(
        "--noise-mode",
        choices=("uniform", "gaussian"),
        default=DEFAULT_NOISE_MODE,
        help="Distribution used for the perturbation.",
    )
    return parser.parse_args()


def build_noise_config(args: argparse.Namespace) -> NoiseConfig:
    if args.noise_ratio < 0.0:
        raise ValueError("--noise-ratio must be non-negative")
    return NoiseConfig(
        ratio=float(args.noise_ratio),
        seed=int(args.noise_seed),
        mode=str(args.noise_mode),
    )


def build_clean_plot_rows(
    metric_key: str,
    read_data: Dict[str, Dict[str, List[dict]]],
    write_data: Dict[str, Dict[str, List[dict]]],
    cache_sizes: List[str],
    all_numjobs: List[int],
) -> List[dict]:
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

    rows.sort(
        key=lambda r: (r["rw"], r["num_jobs"], cache_sort_key(r["cache_size"]), r["qd"])
    )
    return rows


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
        writer.writerow(
            ["rw", "num_jobs", "cache_size", "qd", "metric", "unit", "value"]
        )
        for r in rows:
            writer.writerow(
                [
                    r["rw"],
                    r["num_jobs"],
                    r["cache_size"],
                    r["qd"],
                    metric_key,
                    metric_unit,
                    f"{r['value']:.6f}",
                ]
            )

    print(f"Saved: {csv_path}")


def print_summary(
    op_name: str, dataset: Dict[str, Dict[str, List[dict]]], cache_sizes: List[str]
) -> None:
    print(f"\n{op_name} theoretical summary (best points over NUM_JOBS×QD):")
    for cache in cache_sizes:
        points = dataset[cache]["points"]
        best_iops = max(points, key=lambda x: x["iops"])
        best_lat = min(points, key=lambda x: x["latency_avg"])
        print(f"  [{cache}]")
        print(
            f"    Max IOPS: {best_iops['iops']:.0f} "
            f"(NUM_JOBS={best_iops['num_jobs']}, QD={best_iops['qd']})"
        )
        print(
            f"    Min Avg Lat: {best_lat['latency_avg']:.2f} us "
            f"(NUM_JOBS={best_lat['num_jobs']}, QD={best_lat['qd']})"
        )


def main() -> None:
    args = parse_args()
    noise_config = build_noise_config(args)

    repo_root = Path(__file__).resolve().parents[1]
    output_fig_dir = repo_root / "output-figures"
    output_csv_dir = repo_root / "output-csv"
    output_fig_dir.mkdir(parents=True, exist_ok=True)
    output_csv_dir.mkdir(parents=True, exist_ok=True)

    read_data = build_theoretical_dataset(is_write=False, noise_config=noise_config)
    write_data = build_theoretical_dataset(is_write=True, noise_config=noise_config)
    cache_sizes = sorted(
        set(read_data.keys()) | set(write_data.keys()), key=cache_sort_key
    )
    all_numjobs = NUM_JOBS_SWEEP[:]

    figure_specs = [
        (
            "iops",
            "IOPS (K)",
            "KIOPS",
            "FIO L2P Cache - Theoretical IOPS (Per-NUM_JOBS)",
            "fio_l2p_cache_ideal_iops_plot.png",
        ),
        (
            "latency_avg",
            "Latency (us)",
            "us",
            "FIO L2P Cache - Theoretical Avg Latency (Per-NUM_JOBS)",
            "fio_l2p_cache_ideal_avglat_plot.png",
        ),
    ]

    print(
        "Generating theoretical figures "
        f"(noise={'off' if not noise_config.enabled else f'{noise_config.mode} ±{noise_config.ratio:.1%}, seed={noise_config.seed}'})..."
    )
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
            colors=COLORS,
            all_numjobs=all_numjobs,
            noise_config=noise_config,
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
    print("Theoretical Performance Summary")
    print("=" * 80)
    print_summary("Randread", read_data, cache_sizes)
    print_summary("Randwrite", write_data, cache_sizes)
    print(f"\nAll figures are saved under: {output_fig_dir}")
    print(f"All CSV files are saved under: {output_csv_dir}")


if __name__ == "__main__":
    main()
