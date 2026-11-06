# OpenCode Dynamic Team P0

用于核实动态 Team 插件所需宿主能力的隔离实验。这里保存最小测试插件、宿主驱动和验证结果，不是可用于日常任务的 Team 实现。

基线：OpenCode `3ba597f7769e19cc3b5f67e3d6667e7b6318f6a3`。实验插件只调用公共 `@opencode/plugin` API；驱动使用真实 ServerFetch 和文件数据库，并用确定性的本地模型响应替代外部 LLM。

运行方式和结果在验证完成后补充。临时源码副本、依赖、原始日志和数据库不进入 Git。
