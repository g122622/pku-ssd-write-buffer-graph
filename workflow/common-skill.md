# 1 项目背景

## 1.1 核心目标

在 FEMU 模拟器中构建**DRAM-less SSD 高保真模型**。利用 NVMe HMB 技术划分**L2P 缓存**与**Write Buffer (WB)**，实现去板载 DRAM 及去 SLC Cache 架构。核心范式：**Controller 主导资源分配，Host 负责数据搬运，通过 MCP (Metadata Control Plane) 协同。**

## 1.2 架构改造

### 1.2.1 三层 FTL 缓存体系

* **L1 (SRAM):** 控制器片上缓存，可配置大小，时延设为 0。
* **L2 (HMB):** 主机内存 buffer，存放热点 L2P 表及 WB 数据，引入 PCIe 传输时延。
* **L3 (NAND):** 闪存持久化存储，完整 L2P 表及用户数据，引入 NAND 读写擦时延。
* **时延策略:** 读 Miss 时延累加 (L1+L2+L3+ 回填)，写更新时延取最大 (穿透写入)。
* **替换算法:** 初始实现 LRU，架构支持编译期切换 (ARC 等)。

### 1.2.2 HMB 内存布局

* **区域划分:** `[L2P-L2 区][[WB Data][MCP Ring], [WB Data][MCP Ring]...]`
* **多队列隔离:** 每个 NVMe I/O Queue 独占 `localWB` + `MCP Ring` + `RB-Tree 索引`，消除跨队列锁竞争。
* **配置宏:** `FEMU_HMB_HMMIN_MB`/`HMPRE_MB` 用户显式指定，`FEMU_L2P_L2_SIZE_KB` 独立配置。

## 1.3 关键实现模块

### 1.3.1 HMB 基础设施 (NVMe 1.2 合规)

* **命令支持:** 实现 `Set/Get Features (FID=0x0d)`，校验 `HMDLLA` 16B 对齐，支持 `MR/EHM` 位语义。
* **能力暴露:** Identify Controller 返回 `HMMIN` (64MB)/`HMPRE` (128MB)。
* **描述符解析:** 控制器解析 Host 提交的 HMB Descriptor List，校验 `BSIZE` 累加一致性，保存 GPA 快照。
* **文件封装:** 逻辑抽离至 `hmb.c/h`。

### 1.3.2 Write Buffer (WB) 机制

* **数据结构:**
  * `localWB`: 环形队列，维护 `head/tail/capacity`。
  * `MCP Entry`: 包含 `cmd_id`, `hmb_vaddr`, `length`, `type` (Write Alloc/Read Hit)。
  * `RB-Tree`: 键 `LPN`，值 `hmb_off+len+refcnt+state`，每队列独立锁保护。
* **写流程:** 分配 WB 空间 → 写 MCP 元数据 → 中断通知 Host → Host 拷贝数据 → 发送 `0xd5` 通知 → 后台异步刷盘 (FTL 更新)。
* **读流程:** 查 RB-Tree → Hit (构造 MCP 通知 Host 拷贝) / Miss (NAND 读取) → 发送 `0xd9` 释放 MCP。
* **一致性保障:** `0xd5` 到达后立即镜像 WB 数据至 backend `logical_space`，防止跨队列读旧数据。

### 1.3.3 私有 NVMe 命令扩展

| Opcode | 类型 | 方向 | 功能 |
| :--- | :--- | :--- | :--- |
| **0xd1** | Admin | Host→Ctrl | **WB_HMB_KVA_MAPPING_PUSH**: 推送 HMB 物理地址 (GPA) 到虚拟地址 (KVA) 映射表，避免控制器转换开销。 |
| **0xd5** | I/O | Host→Ctrl | **WB_NOTIFY_COPY_DONE**: 通知 Host 数据拷贝完成，触发 WB 索引更新及异步刷盘。Fire-and-forget。 |
| **0xd9** | I/O | Host→Ctrl | **WB_NOTIFY_READ_DONE**: 通知读数据搬运完成，释放 MCP 条目及 RB-Tree 引用计数。Fire-and-forget。 |

### 1.3.4 Host 驱动改造 (Linux NVMe)

* **内存访问:** 移除 `DMA_ATTR_NO_KERNEL_MAPPING`，确保 HMB 区域内核虚拟地址 (KVA) 可达。
* **KVA 推送:** 初始化阶段发送 `0xd1`，携带 `GPA→KVA` 映射表，失败则降级为纯 L2P HMB 模式。
* **异步搬运:** CQE 解析 `MCP_READY` 标志，下沉数据拷贝至 Per-queue Workqueue，避免中断上下文阻塞。
* **通知提交:** 拷贝完成后异步提交 `0xd5/0xd9`，强制绑定原 `qid`，不等待完成。

## 1.4 并发与一致性策略

* **TRIM 协同 (C-lite 方案):**
  * **原则:** 只回收“绝对安全”LPN (WB 中无忙引用 `refcnt==0`)。
  * **机制:** 忙碌 LPN 跳过回收，设置 `tombstone` 拦截后续 flush 复活。
  * **语义:** 命令必处理，物理回收可延迟，符合 NVMe 规范灵活性。
* **锁机制:** 每队列独立 RB-Tree 锁，无全局大锁。
* **资源管理:** 任何退出路径确保 MCP 回收、WB 空间释放、RB-Tree 节点删除、refcnt 归零。
* **降级策略:** Host 驱动不支持私有命令时，Controller 自动关闭 WB 功能，回退至原生 FEMU 路径。

## 1.5 测试与验证

* **前置条件:** 测试前必须**填盘**，避免 unmapped LPN 导致测试退化为纯元数据路径。
* **工作集:** 1GB 工作集下 L2 缓存容量差异被 NAND 时延淹没 (40us vs 1.2us)，IOPS 受限于单服务点饱和。
* **观测点:** 统计 `mcp_ready_cnt`, `fallback_cnt`, `wb_busy_skipped` 等指标，验证 WB 命中率及 TRIM 安全性。
* **断言检查:** 运行时校验 HMB 边界、MCP 水位、WB 使用率及 Flush 后 refcnt 状态。

## 1.6 遗留与优化

* **FLUSH 命令:** 当前不强制 drain WB，仅做临时策略。
* **0xd1 推送:** 仅初始化发送一次，未处理 reset/resume 重推。
* **错误处理:** MCP 解析失败当前为 fail-fast，未实现单请求透明回退。
* **性能优化:** 后续可实施相邻 LPN 写合并、基于 Channel 并行度的批量回写。

# 2 环境信息

使用嵌套虚拟化：

```
物理机（Windows 11）
├── WSL (Host) 【你当前就在这里！】
 ├── Host DRAM (WSL分配到的内存，当前是 32GB)
 ├── Host OS (WSL Linux，版本是 Ubuntu 24.04.2 LTS on Windows x86_64)
 └── QEMU/FEMU 进程
     ├── FEMU 代码运行用的内存 (如dram.c) (malloc 分配)
     └── Guest VM
         ├── Guest DRAM (虚拟机内存，比如分配 4GB 4核心CPU)
         └── Guest OS (运行在 VM 里的 Linux，版本是 Ubuntu 22.04.5 LTS x86_64)
```

# 3 实验工作流

## 3.1 更改实验变量

如果用户要求的话，则更改实验变量；用户没要求则跳过这一步

## 3.2 启动guest

```bash
cd /home/g122622/dev/FEMU/build-femu/run-blackbox.sh
./femu-compile.sh
./run-blackbox.sh
```

这时候GUSET输出会被重定向到HOST标准输入输出，因此你操作的控制台此时就变成guest而不是host了（但你仍然可以操作当前控制台，所以请不要将其放在后台运行！留在当前控制台就行）。等待十几秒钟之后就可以进行登录了。账号：g122622 密码：123456：

```
 # 登录虚拟机
  sshpass -p '123456' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 8081 g122622@localhost

# 执行远程命令
  sshpass -p '123456' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 8081 g122622@localhost '命令'
```

## 3.3 执行命令

现在就可以开始正式实验了，具体要做什么用户会和你说的。

如果实验分多轮，做完后通过`sudo shutdown -h now`退出guest系统回到host，开始下一轮。

# 4 注意事项

1. 运行过程中输出的日志除了看控制台外，也能通过`/home/g122622/dev/FEMU/build-femu/log`读取（每次启动虚拟机时会重置；但是退出虚拟机后不会重置该文件，而是保留内容）
2. 在正式开始实验前，计算出完成全部实验所需要的大概时间，反馈给用户。需要注意这时候不要停，继续做你的事情。
3. 【重要】执行任务过程若任何一步出错或者你有任何疑问，严禁尝试自行解决，直接停下来让我处置！

# 5 你的任务

请你严格遵循相关要求以及用户描述，开始多轮实验！
