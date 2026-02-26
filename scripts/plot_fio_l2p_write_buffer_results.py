#!/usr/bin/env python3
"""
Plot FIO write-buffer experiment results.

- Parse data from:
  - fio-wb/without_write_buffer.log
  - fio-wb/with_write_buffer.log
- Automatically ignore failed runs (err != 0 / I/O error).
- Normalize units:
  - BW -> MiB/s
  - Latency -> usec
- Draw multi-subplot comparison figure for paper usage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


@dataclass
class FioCase:
    name: str
    group: str
    rw: str
    bs: str
    jobs: int
    qd: int
    err: int
    write_iops: float
    write_bw_mib: float
    write_lat_us: float


def parse_iops(iops_text: str) -> float:
    """Parse fio IOPS field, e.g. 36.2k / 1034."""
    text = iops_text.strip()
    if text.endswith("k"):
        return float(text[:-1]) * 1000.0
    return float(text)


def parse_bw_to_mib(value: str, unit: str) -> float:
    """Convert KiB/s, MiB/s, GiB/s to MiB/s."""
    v = float(value)
    u = unit.strip()
    if u == "KiB/s":
        return v / 1024.0
    if u == "MiB/s":
        return v
    if u == "GiB/s":
        return v * 1024.0
    raise ValueError(f"Unsupported bandwidth unit: {u}")


def parse_latency_to_us(base_unit: str, avg_value: str, suffix: str) -> float:
    """
    Parse clat avg to microseconds.
    fio examples:
      clat (usec): avg=986.67
      clat (nsec): avg=45028.07
      clat (msec): avg=151.90
      avg=1403.4k (rare, with suffix)
    """
    value = float(avg_value)
    suffix = suffix or ""

    # suffix scale in current base unit
    if suffix == "k":
        value *= 1e3
    elif suffix == "m":
        value *= 1e6
    elif suffix == "u":
        value *= 1e-6
    elif suffix == "n":
        value *= 1e-9

    unit = base_unit.lower()
    if unit.startswith("nsec"):
        return value / 1000.0
    if unit.startswith("usec"):
        return value
    if unit.startswith("msec"):
        return value * 1000.0
    if unit.startswith("sec"):
        return value * 1_000_000.0

    # fallback: assume usec
    return value


def infer_group(case_name: str) -> str:
    if case_name.startswith("vol_"):
        return "volume"
    if case_name.startswith("bs_"):
        return "block_size"
    if case_name.startswith("jobs_"):
        return "jobs"
    if case_name.startswith("mode_"):
        return "mode"
    if case_name.startswith("qd_scan_"):
        return "qd_scan"
    return "other"


def parse_single_case(case_name: str, header_args: str, section: str) -> Optional[FioCase]:
    # skip warmup / unrelated
    if case_name == "pre_fill":
        return None

    group = infer_group(case_name)
    if group == "other":
        return None

    kv = dict(re.findall(r"(RW|BS|Jobs|QD|Size|Time)=([^,)]*)", header_args))
    rw = kv.get("RW", "")
    bs = kv.get("BS", "")
    jobs = int(kv.get("Jobs", "1") or "1")
    qd = int(kv.get("QD", "1") or "1")

    # any non-zero err => invalid, ignore this case
    err_match = re.search(rf"{re.escape(case_name)}: \(groupid=.*?\): err=\s*(\d+)", section)
    err = int(err_match.group(1)) if err_match else 0
    if err != 0:
        return None
    if "Input/output error" in section:
        return None

    write_match = re.search(
        r"write:\s+IOPS=([\d.]+k?),\s+BW=([\d.]+)(KiB/s|MiB/s|GiB/s)",
        section,
    )
    if not write_match:
        return None

    write_iops = parse_iops(write_match.group(1))
    write_bw_mib = parse_bw_to_mib(write_match.group(2), write_match.group(3))

    # locate clat avg in write block
    # from write: ... until next operation block or run status
    write_block_match = re.search(
        r"write:.*?(?=\n\s{2}(?:read|trim):|\nRun status group|\Z)",
        section,
        re.DOTALL,
    )
    write_block = write_block_match.group(0) if write_block_match else section

    clat_match = re.search(r"clat \(([^)]+)\):.*?avg=([\d.]+)([kmun]?)", write_block, re.DOTALL)
    if not clat_match:
        return None

    write_lat_us = parse_latency_to_us(
        clat_match.group(1), clat_match.group(2), clat_match.group(3)
    )

    return FioCase(
        name=case_name,
        group=group,
        rw=rw,
        bs=bs,
        jobs=jobs,
        qd=qd,
        err=err,
        write_iops=write_iops,
        write_bw_mib=write_bw_mib,
        write_lat_us=write_lat_us,
    )


def parse_fio_log(log_path: Path) -> Tuple[Dict[str, FioCase], List[str]]:
    content = log_path.read_text(encoding="utf-8", errors="ignore")

    cases: Dict[str, FioCase] = {}
    skipped: List[str] = []

    run_iter = list(re.finditer(r"--- Running:\s+([^\s]+)\s+\((.*?)\)\s+---", content))

    for idx, m in enumerate(run_iter):
        case_name = m.group(1).strip()
        header_args = m.group(2).strip()
        start = m.start()
        end = run_iter[idx + 1].start() if idx + 1 < len(run_iter) else len(content)
        section = content[start:end]

        parsed = parse_single_case(case_name, header_args, section)
        if parsed is None:
            # record failed run only if it has explicit err!=0
            err_match = re.search(rf"{re.escape(case_name)}: \(groupid=.*?\): err=\s*(\d+)", section)
            if err_match and int(err_match.group(1)) != 0:
                skipped.append(case_name)
            continue
        cases[case_name] = parsed

    return cases, skipped


def order_by_group(names: List[str], group: str, case_map: Dict[str, FioCase]) -> List[str]:
    if group == "volume":
        preferred = ["vol_small_seq", "vol_small_rand", "vol_large_seq", "vol_large_rand"]
        return [n for n in preferred if n in names]

    if group == "block_size":
        def bs_key(n: str) -> int:
            bs = case_map[n].bs.lower()
            if bs.endswith("k"):
                return int(bs[:-1])
            if bs.endswith("m"):
                return int(float(bs[:-1]) * 1024)
            return 0

        return sorted(names, key=bs_key)

    if group == "jobs":
        return sorted(names, key=lambda n: case_map[n].jobs)

    if group == "mode":
        preferred = ["mode_write", "mode_randwrite", "mode_rw", "mode_randrw"]
        return [n for n in preferred if n in names]

    if group == "qd_scan":
        return sorted(names, key=lambda n: case_map[n].qd)

    return sorted(names)


def human_label(name: str) -> str:
    return (
        name.replace("vol_", "")
        .replace("bs_", "")
        .replace("jobs_", "jobs=")
        .replace("mode_", "")
        .replace("qd_scan_", "QD=")
        .replace("_", "\n")
    )


def plot_comparison(without_cases: Dict[str, FioCase], with_cases: Dict[str, FioCase], output: Path) -> None:
    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        plt.style.use("ggplot")

    base_color = "#4C78A8"  # without WB
    wb_color = "#F58518"    # with WB

    fig, axes = plt.subplots(3, 2, figsize=(16, 15))
    fig.suptitle("FIO Write Buffer Performance Comparison", fontsize=18, fontweight="bold")

    # ===== 1) Volume - BW =====
    ax = axes[0, 0]
    names = sorted(set(without_cases) & set(with_cases))
    vol_names = order_by_group([n for n in names if without_cases[n].group == "volume"], "volume", without_cases)

    if vol_names:
        x = list(range(len(vol_names)))
        w = 0.38
        bw_wo = [without_cases[n].write_bw_mib for n in vol_names]
        bw_w = [with_cases[n].write_bw_mib for n in vol_names]
        ax.bar([i - w / 2 for i in x], bw_wo, width=w, color=base_color, label="without WB")
        ax.bar([i + w / 2 for i in x], bw_w, width=w, color=wb_color, label="with WB")
        ax.set_xticks(x)
        ax.set_xticklabels([human_label(n) for n in vol_names], rotation=0)
        ax.set_ylabel("Write BW (MiB/s)")
        ax.set_title("(a) Volume tests: bandwidth")
        ax.legend()

    # ===== 2) Volume - latency =====
    ax = axes[0, 1]
    if vol_names:
        x = list(range(len(vol_names)))
        w = 0.38
        lat_wo = [without_cases[n].write_lat_us for n in vol_names]
        lat_w = [with_cases[n].write_lat_us for n in vol_names]
        ax.bar([i - w / 2 for i in x], lat_wo, width=w, color=base_color, label="without WB")
        ax.bar([i + w / 2 for i in x], lat_w, width=w, color=wb_color, label="with WB")
        ax.set_xticks(x)
        ax.set_xticklabels([human_label(n) for n in vol_names], rotation=0)
        ax.set_ylabel("Write clat avg (μs)")
        ax.set_title("(b) Volume tests: latency")
        ax.legend()

    # ===== 3) Block size - BW =====
    ax = axes[1, 0]
    bs_names = order_by_group([n for n in names if without_cases[n].group == "block_size"], "block_size", without_cases)
    if bs_names:
        x = [without_cases[n].bs for n in bs_names]
        y_wo = [without_cases[n].write_bw_mib for n in bs_names]
        y_w = [with_cases[n].write_bw_mib for n in bs_names]
        ax.plot(x, y_wo, marker="o", linewidth=2.2, color=base_color, label="without WB")
        ax.plot(x, y_w, marker="o", linewidth=2.2, color=wb_color, label="with WB")
        ax.set_ylabel("Write BW (MiB/s)")
        ax.set_title("(c) Block size sweep: bandwidth")
        ax.legend()

    # ===== 4) Jobs - IOPS =====
    ax = axes[1, 1]
    jobs_names = order_by_group([n for n in names if without_cases[n].group == "jobs"], "jobs", without_cases)
    if jobs_names:
        x = [without_cases[n].jobs for n in jobs_names]
        y_wo = [without_cases[n].write_iops / 1000.0 for n in jobs_names]
        y_w = [with_cases[n].write_iops / 1000.0 for n in jobs_names]
        ax.plot(x, y_wo, marker="o", linewidth=2.2, color=base_color, label="without WB")
        ax.plot(x, y_w, marker="o", linewidth=2.2, color=wb_color, label="with WB")
        ax.set_xlabel("numjobs")
        ax.set_ylabel("Write IOPS (K)")
        ax.set_title("(d) numjobs sweep: IOPS")
        ax.legend()

    # ===== 5) Mode - BW =====
    ax = axes[2, 0]
    mode_names = order_by_group([n for n in names if without_cases[n].group == "mode"], "mode", without_cases)
    if mode_names:
        x = list(range(len(mode_names)))
        w = 0.38
        y_wo = [without_cases[n].write_bw_mib for n in mode_names]
        y_w = [with_cases[n].write_bw_mib for n in mode_names]
        ax.bar([i - w / 2 for i in x], y_wo, width=w, color=base_color, label="without WB")
        ax.bar([i + w / 2 for i in x], y_w, width=w, color=wb_color, label="with WB")
        ax.set_xticks(x)
        ax.set_xticklabels([human_label(n) for n in mode_names])
        ax.set_ylabel("Write BW (MiB/s)")
        ax.set_title("(e) Access mode comparison")
        ax.legend()

    # ===== 6) Speedup summary (all common valid tests) =====
    ax = axes[2, 1]
    common = [n for n in names if without_cases[n].group in {"volume", "block_size", "jobs", "mode", "qd_scan"}]
    speed_items = []
    for n in common:
        base = without_cases[n].write_bw_mib
        wb = with_cases[n].write_bw_mib
        if base > 0:
            speed_items.append((n, wb / base))

    if speed_items:
        speed_items.sort(key=lambda t: t[1], reverse=True)
        labels = [human_label(n) for n, _ in speed_items]
        vals = [v for _, v in speed_items]
        colors = ["#54A24B" if v >= 1.0 else "#E45756" for v in vals]
        y = list(range(len(vals)))
        ax.barh(y, vals, color=colors, alpha=0.9)
        ax.axvline(1.0, color="black", linestyle="--", linewidth=1.0)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("BW speedup (with/without)")
        ax.set_title("(f) Bandwidth speedup summary")

    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=300, bbox_inches="tight")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    without_log = repo_root / "fio-wb" / "without_write_buffer.log"
    with_log = repo_root / "fio-wb" / "with_write_buffer.log"

    without_cases, without_skipped = parse_fio_log(without_log)
    with_cases, with_skipped = parse_fio_log(with_log)

    print(f"[without WB] valid cases: {len(without_cases)}")
    print(f"[with WB]    valid cases: {len(with_cases)}")

    if without_skipped:
        print("[without WB] skipped failed cases:", ", ".join(sorted(set(without_skipped))))
    if with_skipped:
        print("[with WB] skipped failed cases:", ", ".join(sorted(set(with_skipped))))

    common_valid = sorted(set(without_cases) & set(with_cases))
    print(f"Common valid comparable cases: {len(common_valid)}")

    output = repo_root / "scripts" / "fio_wb_performance_comparison.png"
    plot_comparison(without_cases, with_cases, output)
    print(f"Plot saved to: {output}")


if __name__ == "__main__":
    main()
