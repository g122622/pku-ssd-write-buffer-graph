#!/usr/bin/env python3
"""
FIO L2P Cache Results Plotter
This script parses FIO test results and creates visualization plots
showing performance metrics (bandwidth, IOPS, latency) across different HMB cache sizes.
"""

import re
import os
import matplotlib.pyplot as plt
from pathlib import Path


def parse_iops(iops_text):
    """Parse fio IOPS field, e.g. 98.6k / 1205."""
    text = iops_text.strip()
    if text.endswith('k'):
        return float(text[:-1]) * 1000.0
    return float(text)


def parse_bw_to_mib(value_text, unit_text):
    """Convert fio bandwidth to MiB/s."""
    value = float(value_text)
    unit = unit_text.strip()
    if unit == 'KiB/s':
        return value / 1024.0
    if unit == 'MiB/s':
        return value
    if unit == 'GiB/s':
        return value * 1024.0
    return value


def convert_latency_to_us(value, unit, suffix=''):
    """Convert latency value to microseconds from fio unit/suffix."""
    if suffix == 'k':
        value *= 1000
    elif suffix == 'm':
        value *= 1000000
    elif suffix == 'u':
        value *= 1
    elif suffix == 'n':
        value /= 1000

    if unit in ['nsec', 'nsecs']:
        return value / 1000.0
    if unit in ['msec', 'msecs']:
        return value * 1000.0
    return value

def extract_all_latency_metrics(section):
    """
    提取所有延迟指标并统一单位为微秒
    """
    # 提取clat行
    clat_match = re.search(r'clat \(([^)]+)\):\s+min=([\d.]+)([kmun]*),\s+max=([\d.]+)([kmun]*),\s+avg=([\d.]+)([kmun]*),\s+stdev=([\d.]+)([kmun]*)', section)
    if not clat_match:
        return None
    
    unit = clat_match.group(1)  # 原始单位 (nsec, usec, msec等)
    
    metrics = {}
    values = {
        'min': (float(clat_match.group(2)), clat_match.group(3)),      # 值和后缀
        'max': (float(clat_match.group(4)), clat_match.group(5)),
        'avg': (float(clat_match.group(6)), clat_match.group(7)),
        'stdev': (float(clat_match.group(8)), clat_match.group(9))
    }
    
    for key, (value, suffix) in values.items():
        metrics[key] = convert_latency_to_us(value, unit, suffix)
    
    return metrics


def extract_latency_percentiles(section):
    """提取 90th / 99th / 99.9th 延迟百分位并统一为微秒。"""
    header = re.search(r'clat percentiles \(([^)]+)\):', section)
    if not header:
        return None

    pct_unit = header.group(1)
    metrics = {}
    targets = {
        'latency_p90': '90.00th',
        'latency_p99': '99.00th',
        'latency_p999': '99.90th',
    }

    for key, label in targets.items():
        match = re.search(rf'{re.escape(label)}=\[\s*([\d.]+)([kmun]?)\]', section)
        if not match:
            return None
        value = float(match.group(1))
        suffix = match.group(2)
        metrics[key] = convert_latency_to_us(value, pct_unit, suffix)

    return metrics

def parse_fio_log(log_file):
    """
    Parse FIO log file and extract QD, IOPS, bandwidth, and latency data.
    Returns a dict with the extracted metrics.
    """
    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    data = {
        'qd': [],
        'iops': [],
        'bandwidth': [],  # in MiB/s
        'latency': [],    # avg latency in usec
    }
    
    # Find all QD test sections
    qd_pattern = r'--- Testing QD=(\d+) ---\n.*?read: IOPS=([\d.]+)k?, BW=([\d.]+)MiB/s.*?\n\s+slat.*?\n\s+clat.*?avg=(\d+(?:\.\d+)?)'
    
    matches = re.findall(qd_pattern, content, re.DOTALL)
    
    for match in matches:
        qd = int(match[0])
        iops_str = match[1]
        
        # Parse IOPS (handle 'k' suffix like "92.7k")
        if 'k' in iops_str or ',' in content[content.find(f'QD={qd}'):content.find(f'QD={qd}') + 500]:
            # Try to find IOPS in the format "IOPS=XXXXX"
            iops_match = re.search(
                r'--- Testing QD=' + str(qd) + r' ---.*?read: IOPS=([\d.]+)k?',
                content,
                re.DOTALL
            )
            if iops_match:
                iops_val = float(iops_match.group(1))
                if 'k' in iops_match.group(0).split('\n')[1]:
                    iops_val *= 1000
        else:
            iops_val = float(iops_str) * 1000 if '.' in iops_str else float(iops_str)
        
        bandwidth = float(match[2])
        latency = float(match[3])
        
        data['qd'].append(qd)
        data['iops'].append(iops_val)
        data['bandwidth'].append(bandwidth)
        data['latency'].append(latency)
    
    return data

def parse_fio_log_improved(log_file, op_type='read'):
    """
    Improved version - parse FIO log file more reliably.
    op_type: 'read' or 'write'
    """
    with open(log_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    data = {
        'qd': [],
        'iops': [],
        'bandwidth': [],  # in MiB/s
        'latency_min': [],    # min latency in usec
        'latency_max': [],    # max latency in usec
        'latency_avg': [],    # avg latency in usec
        'latency_p90': [],    # p90 latency in usec
        'latency_p99': [],    # p99 latency in usec
        'latency_p999': [],   # p99.9 latency in usec
    }
    
    # Split by QD test sections
    qd_sections = re.split(r'--- Testing QD=(\d+) ---', content)
    
    # Process pairs of (qd_number, section_content)
    for i in range(1, len(qd_sections), 2):
        if i + 1 < len(qd_sections):
            qd = int(qd_sections[i])
            section = qd_sections[i + 1]
            
            # Extract IOPS
            iops_match = re.search(rf'{op_type}: IOPS=([\d.]+k?)', section)
            bw_match = re.search(r'BW=([\d.]+)(KiB/s|MiB/s|GiB/s)', section)
            latency_metrics = extract_all_latency_metrics(section)
            latency_percentiles = extract_latency_percentiles(section)

            if not iops_match or not bw_match or not latency_metrics or not latency_percentiles:
                continue

            iops_val = parse_iops(iops_match.group(1))
            data['iops'].append(iops_val)
            
            # Extract Bandwidth
            data['bandwidth'].append(parse_bw_to_mib(bw_match.group(1), bw_match.group(2)))
            
            # Extract latency metrics using the new function
            data['latency_min'].append(latency_metrics.get('min', 0))
            data['latency_max'].append(latency_metrics.get('max', 0))
            data['latency_avg'].append(latency_metrics.get('avg', 0))
            data['latency_p90'].append(latency_percentiles.get('latency_p90', 0))
            data['latency_p99'].append(latency_percentiles.get('latency_p99', 0))
            data['latency_p999'].append(latency_percentiles.get('latency_p999', 0))
            
            data['qd'].append(qd)
    
    return data


def build_test_files(data_dir, cache_sizes):
    """Build test file list by timestamp order, mapping to cache sizes."""
    log_files = sorted(Path(data_dir).glob('fio_test_*.log'))
    test_files = []
    for idx, cache_size in enumerate(cache_sizes):
        if idx < len(log_files):
            test_files.append((str(log_files[idx]), cache_size))
    return test_files


def parse_dataset(test_files, op_type):
    """Parse a list of fio log files into dataset dict."""
    all_data = {}
    for log_file, cache_size in test_files:
        label = f"{cache_size} HMB cache" if cache_size != 'DRAM' else 'DRAM (no HMB)'
        print(f"Parsing {op_type} {label} results from {os.path.basename(log_file)}...")
        data = parse_fio_log_improved(log_file, op_type=op_type)
        if data['qd']:
            all_data[cache_size] = data
            print(f"  Found {len(data['qd'])} QD test points")
        else:
            print(f"  Warning: No data found!")
    return all_data


def plot_metric(ax, all_data, cache_sizes, colors, metric_key, ylabel, title):
    """Plot one metric for all cache sizes on a given axis."""
    for idx, cache_size in enumerate(cache_sizes):
        if cache_size in all_data:
            data = all_data[cache_size]
            style = '--' if cache_size == 'DRAM' else '-'
            y_data = data[metric_key]
            if metric_key == 'iops':
                y_data = [iops / 1000 for iops in y_data]
            ax.plot(data['qd'], y_data, color=colors[idx], label=cache_size, linewidth=2, linestyle=style)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title(title, fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

def main():
    cache_sizes = ['512KB', '1024KB', '1536KB', '2048KB', 'DRAM']
    read_test_files = build_test_files(
        'd:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache-randread-4k-1G',
        cache_sizes
    )
    write_test_files = build_test_files(
        'd:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache-randwrite-4k-1G',
        cache_sizes
    )

    # Parse read/write test files
    read_data = parse_dataset(read_test_files, op_type='read')
    write_data = parse_dataset(write_test_files, op_type='write')

    # Create figure with subplots
    fig, axes = plt.subplots(8, 2, figsize=(12, 22))
    fig.suptitle('FIO Performance Results - L2P Cache Size Impact', fontsize=16, fontweight='bold', y=0.985)
    fig.text(
        0.5,
        0.96,
        "Ubuntu 22.04.5 LTS x86_64, Kernel: 5.15.0-170-generic",
        ha="center",
        va="top",
        fontsize=10,
    )
    fig.text(
        0.5,
        0.945,
        "fio-3.28, ioengine=libaio, size=1G, block_size=4K",
        ha="center",
        va="top",
        fontsize=10,
    )
    fig.text(
        0.5,
        0.93,
        "CPU: Intel Core i7-14700KF@5.6GHz, RAM: 128GB",
        ha="center",
        va="top",
        fontsize=10,
    )

    # Color palette for the cache sizes (last is DRAM)
    colors = ["#9bbd5b", "#e4da51", "#eea460", "#e07288", "#8e73f0"]

    # Left column: randread
    plot_metric(axes[0, 0], read_data, cache_sizes, colors, 'bandwidth', 'Bandwidth (MiB/s)', 'Randread: Bandwidth vs Queue Depth')
    plot_metric(axes[1, 0], read_data, cache_sizes, colors, 'iops', 'IOPS (K)', 'Randread: IOPS vs Queue Depth')
    plot_metric(axes[2, 0], read_data, cache_sizes, colors, 'latency_avg', 'Latency (μs)', 'Randread: Avg Latency vs Queue Depth')
    plot_metric(axes[3, 0], read_data, cache_sizes, colors, 'latency_min', 'Latency (μs)', 'Randread: Min Latency vs Queue Depth')
    plot_metric(axes[4, 0], read_data, cache_sizes, colors, 'latency_max', 'Latency (μs)', 'Randread: Max Latency vs Queue Depth')
    plot_metric(axes[5, 0], read_data, cache_sizes, colors, 'latency_p90', 'Latency (μs)', 'Randread: P90 Latency vs Queue Depth')
    plot_metric(axes[6, 0], read_data, cache_sizes, colors, 'latency_p99', 'Latency (μs)', 'Randread: P99 Latency vs Queue Depth')
    plot_metric(axes[7, 0], read_data, cache_sizes, colors, 'latency_p999', 'Latency (μs)', 'Randread: P99.9 Latency vs Queue Depth')

    # Right column: randwrite
    plot_metric(axes[0, 1], write_data, cache_sizes, colors, 'bandwidth', 'Bandwidth (MiB/s)', 'Randwrite: Bandwidth vs Queue Depth')
    plot_metric(axes[1, 1], write_data, cache_sizes, colors, 'iops', 'IOPS (K)', 'Randwrite: IOPS vs Queue Depth')
    plot_metric(axes[2, 1], write_data, cache_sizes, colors, 'latency_avg', 'Latency (μs)', 'Randwrite: Avg Latency vs Queue Depth')
    plot_metric(axes[3, 1], write_data, cache_sizes, colors, 'latency_min', 'Latency (μs)', 'Randwrite: Min Latency vs Queue Depth')
    plot_metric(axes[4, 1], write_data, cache_sizes, colors, 'latency_max', 'Latency (μs)', 'Randwrite: Max Latency vs Queue Depth')
    plot_metric(axes[5, 1], write_data, cache_sizes, colors, 'latency_p90', 'Latency (μs)', 'Randwrite: P90 Latency vs Queue Depth')
    plot_metric(axes[6, 1], write_data, cache_sizes, colors, 'latency_p99', 'Latency (μs)', 'Randwrite: P99 Latency vs Queue Depth')
    plot_metric(axes[7, 1], write_data, cache_sizes, colors, 'latency_p999', 'Latency (μs)', 'Randwrite: P99.9 Latency vs Queue Depth')

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    # Save the figure
    output_path = 'd:\\MiscProjects\\pku-ssd-write-buffer-graph\\scripts\\fio_performance_plot.png'
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"\nPlot saved to: {output_path}")

    # Print summary statistics
    print("\n" + "="*80)
    print("Performance Summary:")
    print("="*80)
    for rw_label, dataset in [('Randread', read_data), ('Randwrite', write_data)]:
        print(f"\n{rw_label}:")
        for cache_size in cache_sizes:
            if cache_size in dataset:
                data = dataset[cache_size]
                if cache_size == 'DRAM':
                    print(f"\nDRAM (no HMB):")
                else:
                    print(f"\n{cache_size} HMB Cache:")
                print(f"  Max Bandwidth:      {max(data['bandwidth']):.2f} MiB/s (at QD={data['qd'][data['bandwidth'].index(max(data['bandwidth']))]})")
                print(f"  Max IOPS:           {max(data['iops']):.0f} (at QD={data['qd'][data['iops'].index(max(data['iops']))]})")
                print(f"  Min Latency:        {min(data['latency_min']):.2f} μs (at QD={data['qd'][data['latency_min'].index(min(data['latency_min']))]})")
                print(f"  Max Latency:        {max(data['latency_max']):.2f} μs (at QD={data['qd'][data['latency_max'].index(max(data['latency_max']))]})")
                print(f"  Avg Latency (min):  {min(data['latency_avg']):.2f} μs (at QD={data['qd'][data['latency_avg'].index(min(data['latency_avg']))]})")

    plt.show()

if __name__ == '__main__':
    main()
