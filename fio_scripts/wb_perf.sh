#!/bin/bash

# ================= 配置区域 =================
DEV="/dev/nvme0n1"
OFFSET="1G"
# 测试区域大小：所有写入将被限制在这个范围内 (例如 1G 起点 + 1G 长度 = 1G~2G 区域)
TEST_AREA_SIZE="2G" 
RESULTS_DIR=~/exp_results
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${RESULTS_DIR}/wb_test_${TIMESTAMP}.log"

# HMB 相关提示
HMB_LIMIT_MB=128
BUFFER_TEST_SMALL="64M"   
BUFFER_TEST_LARGE="1G"    # 注意：不能超过 TEST_AREA_SIZE

# ================= 初始化与安全检查 =================
if [ "$EUID" -ne 0 ]; then 
  echo "请使用 sudo 运行此脚本" 
  exit 1
fi

mkdir -p $RESULTS_DIR

# 获取磁盘总大小进行边界检查 (单位：字节)
DISK_SIZE_BYTES=$(blockdev --getsize64 $DEV 2>/dev/null)
if [ -z "$DISK_SIZE_BYTES" ]; then
    echo "错误：无法获取磁盘 $DEV 的大小"
    exit 1
fi

# 简单换算检查 (脚本层面粗略检查，防止明显越界)
# 1G = 1073741824 字节
OFFSET_BYTES=1073741824
SIZE_BYTES=1073741824
LIMIT_BYTES=$((OFFSET_BYTES + SIZE_BYTES))

# echo "=== 安全写入范围测试脚本 ===" | tee -a $LOG_FILE
# echo "=== 警告：以下 LBA 范围的数据将被覆盖/销毁 ===" | tee -a $LOG_FILE
# echo "设备：$DEV" | tee -a $LOG_FILE
# echo "起始偏移：$OFFSET" | tee -a $LOG_FILE
# echo "测试区域长度：$TEST_AREA_SIZE" | tee -a $LOG_FILE
# echo "================================" | tee -a $LOG_FILE

# read -p "确认上述范围不包含重要数据？(输入 yes 继续): " confirm
# if [ "$confirm" != "yes" ]; then
#     echo "操作已取消。"
#     exit 0
# fi

echo "=== 测试开始于 $(date) ===" | tee -a $LOG_FILE

# 通用 FIO 参数函数
# 参数：$1=name, $2=rw, $3=bs, $4=size, $5=numjobs, $6=iodepth, $7=runtime(可选)
run_fio_job() {
    local name=$1
    local rw=$2
    local bs=$3
    local size=$4
    local jobs=$5
    local qd=$6
    local runtime=$7
    
    # 基础命令
    local base_cmd="sudo fio --name=$name --filename=$DEV --offset=$OFFSET \
        --direct=1 --ioengine=libaio --iodepth=$qd --numjobs=$jobs \
        --rw=$rw --bs=$bs --group_reporting \
        --percentile_list=50:90:95:99:99.9 \
        --output-format=normal"

    # 关键修改：即使有时间限制，也强制指定 size，防止写入溢出到测试区域外
    if [ -n "$size" ]; then
        base_cmd="$base_cmd --size=$size"
    fi

    if [ -n "$runtime" ]; then
        base_cmd="$base_cmd --time_based --runtime=$runtime"
    fi

    echo "--- Running: $name (RW=$rw, BS=$bs, Jobs=$jobs, QD=$qd, Size=$size, Time=$runtime) ---" | tee -a $LOG_FILE
    eval $base_cmd | tee -a $LOG_FILE
    echo "" | tee -a $LOG_FILE
}

# ================= 1. 预填充 (Warm Up & Pre-conditioning) =================
echo "正在预填充数据 ($TEST_AREA_SIZE)..." | tee -a $LOG_FILE
# 确保预填充大小不超过测试区域
run_fio_job "pre_fill" "write" "128k" "$TEST_AREA_SIZE" "1" "64"

# ================= 2. Buffer 容量敏感测试 =================
echo "=== 步骤 2: 写入量 vs Buffer 大小对比 ===" | tee -a $LOG_FILE

# 场景 A: 写入量 < HMB (期望高性能，命中 Buffer)
run_fio_job "vol_small_seq" "write" "128k" "$BUFFER_TEST_SMALL" "1" "32"
run_fio_job "vol_small_rand" "randwrite" "4k" "$BUFFER_TEST_SMALL" "1" "32"

# 场景 B: 写入量 > HMB (期望性能下降，Buffer 溢出，直写 NAND 或触发 GC) (但仍在 TEST_AREA_SIZE 范围内)
run_fio_job "vol_large_seq" "write" "128k" "$BUFFER_TEST_LARGE" "1" "32"
run_fio_job "vol_large_rand" "randwrite" "4k" "$BUFFER_TEST_LARGE" "1" "32"

# ================= 3. 多维负载矩阵测试 =================
echo "=== 步骤 3: 不同读写模式/块大小/线程数矩阵 ===" | tee -a $LOG_FILE
RUNTIME=30 

# 3.1 不同块大小 (BS) - 限制在 TEST_AREA_SIZE 内循环写入
for bs in 4k 16k 128k 1M; do
    # 修改点：传入 $TEST_AREA_SIZE 作为 size 参数
    run_fio_job "bs_${bs}" "randwrite" "$bs" "$TEST_AREA_SIZE" "1" "32" "$RUNTIME"
done

# 3.2 不同线程数 (Numjobs)
for jobs in 1 2 4; do
    # 修改点：传入 $TEST_AREA_SIZE 作为 size 参数
    run_fio_job "jobs_${jobs}" "randwrite" "4k" "$TEST_AREA_SIZE" "$jobs" "1" "$RUNTIME"
done

# 3.3 不同读写混合模式
for rw in "write" "randwrite" "rw" "randrw"; do
    # 修改点：传入 $TEST_AREA_SIZE 作为 size 参数
    run_fio_job "mode_${rw}" "$rw" "4k" "$TEST_AREA_SIZE" "1" "32" "$RUNTIME"
done

# ================= 4. 队列深度 (QD) 扫描 =================
echo "=== 步骤 4: 写入 QD 扫描测试 ===" | tee -a $LOG_FILE
for qd in 1 2 4 8 16 32 64; do
    # 修改点：传入 $TEST_AREA_SIZE 作为 size 参数
    run_fio_job "qd_scan_$qd" "randwrite" "4k" "$TEST_AREA_SIZE" "1" "$qd" "10"
done

echo "=== 测试完成于 $(date) ===" | tee -a $LOG_FILE
echo "所有结果已保存到：$LOG_FILE" | tee -a $LOG_FILE
echo "安全提示：测试仅影响了 $OFFSET 开始的 $TEST_AREA_SIZE 区域。"