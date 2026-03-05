实验目的：探究write-buffer启用前后对ssd随机写稳态性能的影响

本实验有两轮，第一轮不加载自定义nvme驱动(WB_Disabled)，第二次要加载（WB_Enabled）。

怎么加载驱动呢？很简单：guest确认登录成功后，执行：

```
cd /usr/src/linux-source-5.15.0/drivers/nvme/host
sudo rmmod nvme
sudo insmod ./nvme.ko
```

你需要执行`/mnt/wsl-share/fio_scripts`下的`wb_steady_state.sh`(`/mnt/wsl-share/fio_scripts/wb_steady_state.sh`)。脚本会自动跑并写入结果到指定文件（可能不止产生一个文件）。(注意：脚本由wsl host共享，在wsl中位于`/mnt/d/MiscProjects/pku-ssd-write-buffer-graph/fio_scripts`下)

文件写入后你需要在本轮实验生成的**所有文件**的文件名末尾加上后缀-WB_Disabled or -WB_Enabled，表示此轮实验是否启用write buffer，如原文件名为xxxxxx.log，则修改后为：xxxxxx（原文件名）-WB_Disabled.log 注意这个目录可能已经现存了一些文件，不要动已有log文件！
