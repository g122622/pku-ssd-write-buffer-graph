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
        # 处理后缀 (k, m, u, n等)
        if suffix == 'k':
            value *= 1000
        elif suffix == 'm':
            value *= 1000000
        elif suffix == 'u':
            value *= 1  # 微秒乘1，不变
        elif suffix == 'n':
            value /= 1000  # 纳秒转微秒
        
        # 根据原始单位转换为微秒
        if unit in ['nsec', 'nsecs']:
            value /= 1000.0
        elif unit in ['msec', 'msecs']:
            value *= 1000.0
        elif unit in ['usec', 'usecs']:
            pass  # 已经是微秒，不需要转换
        # 对于其他单位，保持原样
        
        metrics[key] = value
    
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

def parse_fio_log_improved(log_file):
    """
    Improved version - parse FIO log file more reliably.
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
    }
    
    # Split by QD test sections
    qd_sections = re.split(r'--- Testing QD=(\d+) ---', content)
    
    # Process pairs of (qd_number, section_content)
    for i in range(1, len(qd_sections), 2):
        if i + 1 < len(qd_sections):
            qd = int(qd_sections[i])
            section = qd_sections[i + 1]
            
            # Extract IOPS
            iops_match = re.search(r'read: IOPS=([\d.]+)k?', section)
            if iops_match:
                iops_val = float(iops_match.group(1))
                if 'k' in iops_match.group(0):
                    iops_val *= 1000
                data['iops'].append(iops_val)
            
            # Extract Bandwidth
            bw_match = re.search(r'BW=([\d.]+)MiB/s', section)
            if bw_match:
                data['bandwidth'].append(float(bw_match.group(1)))
            
            # Extract latency metrics using the new function
            latency_metrics = extract_all_latency_metrics(section)
            if latency_metrics:
                data['latency_min'].append(latency_metrics.get('min', 0))
                data['latency_max'].append(latency_metrics.get('max', 0))
                data['latency_avg'].append(latency_metrics.get('avg', 0))
            
            data['qd'].append(qd)
    
    return data

def main():
    # Define the test configurations
    test_files = [
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055009.log', '512KB'),
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055221.log', '1024KB'),
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055425.log', '1536KB'),
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055607.log', '2048KB'),
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_064412.log', 'DRAM'),
    ]

    # Parse all test files
    all_data = {}
    for log_file, cache_size in test_files:
        label = f"{cache_size} HMB cache" if cache_size != 'DRAM' else 'DRAM (no HMB)'
        print(f"Parsing {label} results from {os.path.basename(log_file)}...")
        data = parse_fio_log_improved(log_file)
        if data['qd']:
            all_data[cache_size] = data
            print(f"  Found {len(data['qd'])} QD test points")
        else:
            print(f"  Warning: No data found!")

    # Create figure with subplots
    fig, axes = plt.subplots(5, 1, figsize=(5, 14))
    fig.suptitle('FIO Performance Results - L2P Cache Size Impact', fontsize=16, fontweight='bold')
    # 副标题
    # fig.text(0.5, 0.94, "fio-3.28 size=1G block_size=4K", ha="center", fontsize=12)
    # Color palette for the cache sizes (last is DRAM)
    colors = ["#9bbd5b", "#e4da51", "#eea460", "#e07288", "#8e73f0"]
    cache_sizes = ['512KB', '1024KB', '1536KB', '2048KB', 'DRAM']

    # Plot 1: Bandwidth
    ax = axes[0]
    for idx, cache_size in enumerate(cache_sizes):
        if cache_size in all_data:
            data = all_data[cache_size]
            style = '--' if cache_size == 'DRAM' else '-'
            ax.plot(data['qd'], data['bandwidth'], color=colors[idx], label=cache_size, linewidth=2, linestyle=style)
    ax.set_ylabel('Bandwidth (MiB/s)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('Bandwidth vs Queue Depth', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

    # Plot 2: IOPS
    ax = axes[1]
    for idx, cache_size in enumerate(cache_sizes):
        if cache_size in all_data:
            data = all_data[cache_size]
            style = '--' if cache_size == 'DRAM' else '-'
            ax.plot(data['qd'], [iops/1000 for iops in data['iops']], color=colors[idx], label=cache_size, linewidth=2, linestyle=style)
    ax.set_ylabel('IOPS (K)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('IOPS vs Queue Depth', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

    # Plot 3: Min Latency
    ax = axes[3]
    for idx, cache_size in enumerate(cache_sizes):
        if cache_size in all_data:
            data = all_data[cache_size]
            style = '--' if cache_size == 'DRAM' else '-'
            ax.plot(data['qd'], data['latency_min'], color=colors[idx], label=cache_size, linewidth=2, linestyle=style)
    ax.set_ylabel('Latency (μs)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('Min Latency vs Queue Depth', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

    # Plot 4: Max Latency
    ax = axes[4]
    for idx, cache_size in enumerate(cache_sizes):
        if cache_size in all_data:
            data = all_data[cache_size]
            style = '--' if cache_size == 'DRAM' else '-'
            ax.plot(data['qd'], data['latency_max'], color=colors[idx], label=cache_size, linewidth=2, linestyle=style)
    ax.set_ylabel('Latency (μs)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('Max Latency vs Queue Depth', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

    # Plot 5: Avg Latency
    ax = axes[2]
    for idx, cache_size in enumerate(cache_sizes):
        if cache_size in all_data:
            data = all_data[cache_size]
            style = '--' if cache_size == 'DRAM' else '-'
            ax.plot(data['qd'], data['latency_avg'], color=colors[idx], label=cache_size, linewidth=2, linestyle=style)
    ax.set_ylabel('Latency (μs)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('Avg Latency vs Queue Depth', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

    plt.tight_layout()

    # Save the figure
    output_path = 'd:\\MiscProjects\\pku-ssd-write-buffer-graph\\scripts\\fio_performance_plot.png'
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"\nPlot saved to: {output_path}")

    # Print summary statistics
    print("\n" + "="*80)
    print("Performance Summary:")
    print("="*80)
    for cache_size in cache_sizes:
        if cache_size in all_data:
            data = all_data[cache_size]
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
