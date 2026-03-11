def calc_iops(num_jobs, qd, l1_size, l2_size, is_write):
    return _calc_model(num_jobs, qd, l1_size, l2_size, is_write)[0]


def calc_avg_lat(num_jobs, qd, l1_size, l2_size, is_write):
    return _calc_model(num_jobs, qd, l1_size, l2_size, is_write)[1]


def _calc_model(num_jobs, qd, l1_size, l2_size, is_write):
    """
    FEMU BBSSD theoretical model for the given fixed configuration:

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
    - For the read workload in your fio script, this matches the FEMU device-side
      timing model closely.
    - For writes, this models the same code path but intentionally does NOT add
      GC-state-dependent extra cost, because GC depends on prior workload history
      and cannot be inferred from this stateless function signature.
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
