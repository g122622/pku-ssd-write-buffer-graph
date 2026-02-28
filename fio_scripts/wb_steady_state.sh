#!/bin/bash

cd /usr/src/linux-source-5.15.0/drivers/nvme/host
sudo rmmod nvme
sudo insmod ./nvme.ko

# ================= 配置区域 =================
DEV="/dev/nvme0n1"       # ⚠️ 请再次确认设备名！
OFFSET="1G"              # 起始偏移量
TEST_SIZE="4G"           # 测试区域大小 (1G 到 5G，即 4GB)
RUNTIME=60              # 测试运行时长 (秒)。想看稳态可能需要设长一点，比如 1200 (20分钟) 或更久
BS="4k"                  # 块大小，测 IOPS 必须用 4k
IODEPTH=128               # 队列深度
RESULTS_DIR=~/exp_results
# ===========================================

# 创建结果目录
mkdir -p $RESULTS_DIR
cd $RESULTS_DIR

# 获取时间戳
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_PREFIX="${RESULTS_DIR}/ssd_test_${TIMESTAMP}"

echo "=== SSD 稳态测试开始 ==="
echo "设备: $DEV"
echo "测试范围: Offset=$OFFSET, Size=$TEST_SIZE"
echo "模式: 随机写入 (randwrite), 块大小=$BS"
echo "时长: ${RUNTIME} 秒"
echo "注意：为了看到 Steady State，脚本会在该 4GB 区域内反复覆盖写入。"
echo "================================"

# ⚠️ 关键：确保测试前该区域是空的 (Trimmed)，这样才能测出 FOB 性能
# 如果这是新盘或刚格式化过的盘，通常已经是 FOB 状态。
# 如果不确定，可以使用 nvme cli 对全盘 trim (危险操作，慎用)，或者确保没写过数据。
# 这里我们假设用户已经准备好了干净的盘。

# 执行 FIO 测试
# --time_based: 必须加！这会让 FIO 在 4GB 空间内反复写，直到达到 RUNTIME 时间。
# --iops_log: 生成 IOPS 随时间变化的日志，这是画图的关键。
sudo fio --name=ssd_steady_state \
    --filename=$DEV \
    --offset=$OFFSET \
    --size=$TEST_SIZE \
    --rw=randwrite \
    --bs=$BS \
    --direct=1 \
    --ioengine=libaio \
    --iodepth=$IODEPTH \
    --numjobs=1 \
    --time_based \
    --runtime=$RUNTIME \
    --group_reporting \
    --write_iops_log=${LOG_PREFIX}_iops.log \
    --log_avg_msec=500 \
    --output-format=json+ | tee ${LOG_PREFIX}_result.json

echo ""
echo "=== 测试完成 ==="
echo "IOPS 日志已保存至: ${LOG_PREFIX}_iops.log"
echo "请使用下方的 Python 脚本绘图。"