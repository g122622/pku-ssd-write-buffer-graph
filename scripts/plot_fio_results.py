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
        'latency': [],    # avg latency in usec
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
            
            # Extract average latency from clat (completion latency)
            clat_match = re.search(r'clat \(.*?\): min=\d+(?:\.?\d+)?(?:k|m|u|n)?, max=\d+(?:\.?\d+)?(?:k|m|u|n)?, avg=([\d.]+)', section)
            if clat_match:
                data['latency'].append(float(clat_match.group(1)))
            
            data['qd'].append(qd)
    
    return data

def main():
    # Define the test configurations
    test_files = [
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055009.log', '512KB'),
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055221.log', '1024KB'),
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055425.log', '1536KB'),
        ('d:\\MiscProjects\\pku-ssd-write-buffer-graph\\fio-l2p-cache\\fio_test_20260226_055607.log', '2048KB'),
    ]

    # Parse all test files
    all_data = {}
    for log_file, cache_size in test_files:
        print(f"Parsing {cache_size} HMB cache results from {os.path.basename(log_file)}...")
        data = parse_fio_log_improved(log_file)
        if data['qd']:
            all_data[cache_size] = data
            print(f"  Found {len(data['qd'])} QD test points")
        else:
            print(f"  Warning: No data found!")

    # Create figure with subplots
    # Reduce figure width to 30% of original (original width 12 -> new width 3.6)
    fig, axes = plt.subplots(3, 1, figsize=(5, 10))
    fig.suptitle('FIO Performance Results - L2P Cache Size Impact', fontsize=16, fontweight='bold')
    # Color palette for the four cache sizes
    colors = ["#9bbd5b", "#e4da51", "#eea460", "#e07288"]

    # Plot 1: Bandwidth
    ax = axes[0]
    for idx, cache_size in enumerate(['512KB', '1024KB', '1536KB', '2048KB']):
        if cache_size in all_data:
            data = all_data[cache_size]
            ax.plot(data['qd'], data['bandwidth'], color=colors[idx], label=cache_size, linewidth=2)
    ax.set_ylabel('Bandwidth (MiB/s)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('Bandwidth vs Queue Depth', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

    # Plot 2: IOPS
    ax = axes[1]
    for idx, cache_size in enumerate(['512KB', '1024KB', '1536KB', '2048KB']):
        if cache_size in all_data:
            data = all_data[cache_size]
            ax.plot(data['qd'], [iops/1000 for iops in data['iops']], color=colors[idx], label=cache_size, linewidth=2)
    ax.set_ylabel('IOPS (K)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('IOPS vs Queue Depth', fontsize=13)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=10)

    # Plot 3: Latency
    ax = axes[2]
    for idx, cache_size in enumerate(['512KB', '1024KB', '1536KB', '2048KB']):
        if cache_size in all_data:
            data = all_data[cache_size]
            ax.plot(data['qd'], data['latency'], color=colors[idx], label=cache_size, linewidth=2)
    ax.set_ylabel('Latency (μs)', fontsize=12)
    ax.set_xlabel('Queue Depth (QD)', fontsize=12)
    ax.set_title('Latency (avg) vs Queue Depth', fontsize=13)
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
    for cache_size in ['512KB', '1024KB', '1536KB', '2048KB']:
        if cache_size in all_data:
            data = all_data[cache_size]
            print(f"\n{cache_size} HMB Cache:")
            print(f"  Max Bandwidth: {max(data['bandwidth']):.2f} MiB/s (at QD={data['qd'][data['bandwidth'].index(max(data['bandwidth']))]})")
            print(f"  Max IOPS:      {max(data['iops']):.0f} (at QD={data['qd'][data['iops'].index(max(data['iops']))]})")
            print(f"  Min Latency:   {min(data['latency']):.2f} μs (at QD={data['qd'][data['latency'].index(min(data['latency']))]})")

    plt.show()

if __name__ == '__main__':
    main()
