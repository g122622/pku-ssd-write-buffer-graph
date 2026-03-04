实验目的：探究l2p_l2_size_kb对ssd随机读写性能的影响

每轮实验循环前你需要修改：/home/g122622/dev/FEMU/build-femu/run-blackbox.sh中的l2p_l2_size_kb设置项，从 512 *1 到 512* 2 到 512 *3 到 512* 4，全部做完后就可以结束了。

guest确认登录成功后你需要执行`/mnt/wsl-share/fio_scripts`下的`fio-l2p-cache-randread-4k-1G.sh`和`fio-l2p-cache-randwrite-4k-1G.sh`。脚本会自动跑并写入结果到指定文件。(注意：这两个脚本由wsl host共享，在wsl中位于`/mnt/d/MiscProjects/pku-ssd-write-buffer-graph/fio_scripts`下)

文件写入后你需要在文件名末尾加上后缀-L2SizexxxKB，表示此轮实验的L2缓存大小，如原文件名为xxxxxx.log，则修改后为：xxxxxx（原文件名）-L2SizexxxKB.log 注意这个目录已经现存了一些文件，不要动已有log文件！
