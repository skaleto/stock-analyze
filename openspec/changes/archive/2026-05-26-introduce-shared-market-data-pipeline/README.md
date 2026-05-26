# introduce-shared-market-data-pipeline

拉数据从两个 agent 各跑一遍，改成一个独立的 prepare-market-data 任务先跑，agent 通过 offline_mode 只读共享缓存；systemd 通过 ExecStartPost 链式触发
