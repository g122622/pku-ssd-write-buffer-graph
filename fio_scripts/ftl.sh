#!/bin/bash
DEV="/dev/nvme0n1"
OFFSET="1G"
SIZE="1G"

# 创建结果目录（如果不存在）
RESULTS_DIR=~/exp_results
mkdir -p $RESULTS_DIR

# 获取当前时间戳，用于文件名
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${RESULTS_DIR}/fio_test_${TIMESTAMP}.log"

echo "=== 测试开始于 $(date) ===" | tee -a $LOG_FILE
echo "=== 测试配置 ===" | tee -a $LOG_FILE
echo "DEV: $DEV" | tee -a $LOG_FILE
echo "OFFSET: $OFFSET" | tee -a $LOG_FILE
echo "SIZE: $SIZE" | tee -a $LOG_FILE
echo "日志文件: $LOG_FILE" | tee -a $LOG_FILE
echo "================================" | tee -a $LOG_FILE

echo "=== 开始预填充 (Pre-conditioning) ===" | tee -a $LOG_FILE
sudo fio --name=pre --filename=$DEV --offset=$OFFSET --size=$SIZE \
    --rw=write --bs=4k --direct=1 --ioengine=libaio --iodepth=64 \
    --group_reporting | tee -a $LOG_FILE

echo "=== 开始 QD 扫描测试 ===" | tee -a $LOG_FILE
for qd in 1 2 4 6 8 10 12 14 16 18 20 22 24 26 28 30 32; do
    echo "--- Testing QD=$qd ---" | tee -a $LOG_FILE
    
    sudo fio --name=test_qd$qd --filename=$DEV --offset=$OFFSET --size=$SIZE \
        --direct=1 --rw=randread --bs=4k \
        --ioengine=libaio --iodepth=$qd \
        --numjobs=1 --time_based --runtime=3 \
        --group_reporting | tee -a $LOG_FILE
    
    echo "" | tee -a $LOG_FILE
done

echo "=== 测试完成于 $(date) ===" | tee -a $LOG_FILE
echo "所有结果已保存到: $LOG_FILE" | tee -a $LOG_FILE

