# opencode-agentteam

OpenCode 动态 Agent Team 插件项目：由主模型按任务生成成员规格，不固定枚举专业角色。

当前处于 P0 验证阶段，仓库保存最小测试插件、宿主驱动和验证结果，尚未实现用于日常任务的完整 Team 功能。

项目定位、社区方案比较、架构设计及 P0–P4 实施步骤见[项目调研与实施计划](docs/research-plan.md)。

强弱模型如何通过协作逐步确定可委派的任务类型和规模，见补充设计[模型任务范围校准](docs/model-capability-calibration.md)。该方向尚未实现或验证效果。

基线：OpenCode `3ba597f7769e19cc3b5f67e3d6667e7b6318f6a3`。实验插件只调用公共 `@opencode/plugin` API；驱动使用真实 ServerFetch 和文件数据库，并用确定性的本地模型响应替代外部 LLM。

2026-11-06 验证结果：**13 项插件集成检查、142 项选定宿主测试、3 项 SQLite 检查通过**。详见 [P0 结果与设计决策](docs/p0-results.md)，机器可读摘要位于 [evidence/2026-11-06.json](evidence/2026-11-06.json)。

## 复现

需要 Python 3、Git、tar 和 Bun。本次使用 Bun 1.4.2；源码的 packageManager 声明为 1.4.4，后者尚未重跑。实验默认拒绝宿主 HTTP 外连，没有使用真实模型或用户凭证。

先导出基线并安装依赖。脚本打印临时源码目录；安装使用锁文件并禁用生命周期脚本，不改原 checkout：

```bash
python3 prepare.py --repository ../opencode --install
```

把下面的 `/tmp/EXPORTED_SNAPSHOT` 换成打印出的实际目录：

```bash
python3 run.py --opencode /tmp/EXPORTED_SNAPSHOT --source-commit 3ba597f7769e19cc3b5f67e3d6667e7b6318f6a3
bun sqlite-probe.ts
```

`run.py` 将驱动复制到隔离副本的 `packages/server/p0`，通过真实 ServerFetch、公共 RPC 和插件接口工作。运行中启动两个独立宿主进程，先对第一个进程执行 SIGKILL，再用同一数据库启动第二个。并行检查用两个模型请求同时等待显式放行证明并发，不以耗时猜测。

结果保存在 `results/<时间>/report.json` 和对应日志中。临时数据库路径记录在 report 的 state 字段，便于失败排查。运行结束后脚本关闭自己启动的宿主；临时源码、数据库和原始日志不纳入 Git，可在不再需要排查时删除。

选定的宿主测试在隔离副本的 `packages/core` 下运行：

```bash
bun run script/test.ts test/plugin/session-instructions.test.ts test/session-create.test.ts test/session-prompt.test.ts test/session-instructions.test.ts test/agent.test.ts test/session-run-coordinator.test.ts
```

## 范围

- `plugin/index.ts` 是公开插件接口的探针，包含测试用 RPC、可控模型响应和测试数据库；不要安装进日常 OpenCode 配置。
- `worker.ts` 仅负责搭建隔离宿主和传输测试请求；它使用源码中的 ServerFetch，不是未来产品插件的依赖。
- `sqlite-probe.ts` 验证两个进程竞争版本、事务回滚以及任务与通知同次提交，未实现完整任务板。
- 本次确认的是给定源码基线上的运行行为；npm 发布包、Node 运行时、其他平台和真实 LLM 协作质量尚未验证。

初步架构选择为：Session 专属 MemberSpec、原生 Inbox、插件自有 SQLite 事务存储。持久日志恢复需要公共完整客户端或等价适配；`ctx.session` 没有直接暴露 Inbox / log 查询。
