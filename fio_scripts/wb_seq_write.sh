#!/bin/bash

cd /usr/src/linux-source-5.15.0/drivers/nvme/host
sudo rmmod nvme
sudo insmod ./nvme.ko

# ================= 配置区域 =================
DEV="/dev/nvme0n1"       # ⚠️ 请再次确认设备名！
OFFSET="1G"             # 起始偏移量，保护分区表
MAX_SIZE=192            # 测试的最大容量 (MB)
STEP=8                  # 步长
BS="1M"                 # 单次 IO 块大小 (测试带宽建议 1M)
RESULTS_DIR=~/exp_results
# ===========================================

# 创建结果目录
mkdir -p $RESULTS_DIR

# 获取时间戳
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${RESULTS_DIR}/bw_test_${TIMESTAMP}.log"

echo "=== 测试开始于 $(date) ===" | tee -a $LOG_FILE
echo "=== 测试配置 ===" | tee -a $LOG_FILE
echo "DEV: $DEV" | tee -a $LOG_FILE
echo "OFFSET: $OFFSET" | tee -a $LOG_FILE
echo "BLOCK_SIZE: $BS" | tee -a $LOG_FILE
echo "测试范围：8MB 到 ${MAX_SIZE}MB (步长 ${STEP}MB)" | tee -a $LOG_FILE
echo "日志文件：$LOG_FILE" | tee -a $LOG_FILE
echo "================================" | tee -a $LOG_FILE

# 预填充 (Pre-conditioning)
# 为了确保测试区域已分配，先写入比最大测试值稍大的数据 (这里保持 1G 足够覆盖 192M)
echo "=== 开始预填充 (Pre-conditioning) ===" | tee -a $LOG_FILE
echo "正在写入 1G 数据以初始化测试区域..." | tee -a $LOG_FILE
sudo fio --name=pre --filename=$DEV --offset=$OFFSET --size=1G \
    --rw=write --bs=1M --direct=1 --ioengine=libaio --iodepth=64 \
    --group_reporting --name=precondition | tee -a $LOG_FILE

echo "=== 开始不同容量的顺序写入带宽测试 ===" | tee -a $LOG_FILE

# 循环测试：8, 16, 24 ... 192
for size in $(seq $STEP $STEP $MAX_SIZE); do
    echo "" | tee -a $LOG_FILE
    echo "--- Testing Total Size = ${size}MB ---" | tee -a $LOG_FILE
    
    # 关键参数说明：
    # --rw=write      : 顺序写入
    # --bs=1M         : 1M 块大小，适合测带宽
    # --size=${size}M : 本次测试写入的总数据量
    # --direct=1      : 绕过系统缓存，测磁盘真实性能
    # --numjobs=1     : 单线程
    sudo fio --name=test_size_${size}M \
        --filename=$DEV \
        --offset=$OFFSET \
        --size=${size}M \
        --direct=1 \
        --rw=write \
        --bs=$BS \
        --ioengine=libaio \
        --iodepth=64 \
        --numjobs=1 \
        --group_reporting | tee -a $LOG_FILE
    
    # 可选：如果测试 SSD SLC 缓存，有时需要在每次测试间 trim，但 fio 对块设备 trim 支持有限
    # 此处采用连续写入模式，模拟持续写入场景
done

echo "" | tee -a $LOG_FILE
echo "=== 测试完成于 $(date) ===" | tee -a $LOG_FILE
echo "所有结果已保存到：$LOG_FILE" | tee -a $LOG_FILE