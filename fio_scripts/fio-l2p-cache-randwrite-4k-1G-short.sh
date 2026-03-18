#!/bin/bash
DEV="/dev/nvme0n1"
OFFSET="1G"
SIZE="1G"

# 创建结果目录（如果不存在）
RESULTS_DIR=/mnt/wsl-share/results/fio-l2p-cache-randwrite-4k-1G
mkdir -p $RESULTS_DIR

# 获取当前时间戳，用于文件名
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="${RESULTS_DIR}/fio_uring_test_${TIMESTAMP}.log"

echo "=== 测试开始于 $(date) ===" | tee -a $LOG_FILE
echo "=== 测试配置 ===" | tee -a $LOG_FILE
echo "DEV: $DEV" | tee -a $LOG_FILE
echo "OFFSET: $OFFSET" | tee -a $LOG_FILE
echo "SIZE: $SIZE" | tee -a $LOG_FILE
echo "IO Engine: io_uring" | tee -a $LOG_FILE
echo "日志文件：$LOG_FILE" | tee -a $LOG_FILE
echo "================================" | tee -a $LOG_FILE

# 检查 io_uring 支持 (可选，防止命令直接报错)
if ! sudo fio --enghelp | grep -q io_uring; then
    echo "警告：当前 fio 版本可能不支持 io_uring 引擎" | tee -a $LOG_FILE
fi

echo "=== 开始预填充 (Pre-conditioning) ===" | tee -a $LOG_FILE
# 预填充也使用 io_uring 保持一致性
sudo fio --name=pre --filename=$DEV --offset=$OFFSET --size=$SIZE \
    --rw=write --bs=4k --direct=1 --ioengine=io_uring --iodepth=64 \
    --group_reporting | tee -a $LOG_FILE

sleep 3s

echo "=== 开始测试 ===" | tee -a $LOG_FILE

# --- 测试用例 1 ---
echo "========== Testing 1 ==========" | tee -a $LOG_FILE
sudo fio --name=test_1 --filename=$DEV --offset=$OFFSET --size=$SIZE \
    --direct=1 --rw=randwrite --bs=4k \
    --ioengine=io_uring --iodepth=1 \
    --numjobs=1 --time_based --runtime=10 \
    --group_reporting | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE

# # --- 测试用例 2 ---
# echo "========== Testing 2 ==========" | tee -a $LOG_FILE
# sudo fio --name=test_2 --filename=$DEV --offset=$OFFSET --size=$SIZE \
#     --direct=1 --rw=randwrite --bs=4k \
#     --ioengine=io_uring --iodepth=1 \
#     --numjobs=64 --time_based --runtime=10 \
#     --group_reporting | tee -a $LOG_FILE

# echo "" | tee -a $LOG_FILE

echo "=== 测试完成于 $(date) ===" | tee -a $LOG_FILE
echo "所有结果已保存到：$LOG_FILE" | tee -a $LOG_FILE