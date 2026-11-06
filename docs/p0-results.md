# 动态 Team 插件 P0 实测结果

日期：2026-11-06。结论：**本地基线上的核心插件可行性通过，可以进入成员与通信层实现。** 产品插件、任务板和发布包兼容性尚未完成；本次没有修改 OpenCode 业务源码。

项目架构和后续阶段见[调研与实施计划](research-plan.md)。

## 1. 环境与方法

| 项目 | 实际值 |
|---|---|
| OpenCode commit | `3ba597f7769e19cc3b5f67e3d6667e7b6318f6a3` |
| 平台 | Linux x64 |
| Bun | 1.4.2；源码声明 1.4.4，未在后者重跑 |
| 依赖 | 从该 commit 导出临时源码，按 frozen lockfile 安装 core / server 所需依赖，禁用生命周期脚本 |
| 插件加载 | 本地包通过 `@opencode/plugin` 公共导出加载，按 Location 实例化 |
| 宿主 | 原生 ServerFetch 路由、真实 Session 执行、文件 SQLite 数据库；未连接日常服务 |
| 模型 | 本地确定性 LanguageModelV3 测试实现，执行循环、工具、压缩仍使用真实宿主 |
| 网络 | 拒绝宿主 HTTP 请求；本地模型自动发现的三类探测被拦截，没有实际联系现有服务 |
| 配置与凭证 | 使用临时目录、显式全局服务路径和白名单子进程环境，没有加载用户模型凭证 |

最初直接运行现有 checkout 的测试失败：安装的 `@effect/platform-node` 为 4.0.1，源码锁定 4.1.0，缺少 `NodeWS` 导出。依赖在隔离副本中安装后解决；没有因此更改原 checkout 的 node_modules。初期探针还修正了 RPC 返回值 JSON 化、模型屏障释放及压缩摘要格式，最终结果以修正后的完整运行计。

## 2. 验证结果

### 2.1 真实宿主上的公共插件探针：13 项通过

| 检查 | 实测事实 | 对设计的影响 |
|---|---|---|
| 公开插件加载 | 公共包导出可导入，setup、工具、模型 hook、RPC 注册有效 | 可使用当前公共 Promise 插件 API |
| 子 Session 创建与 ID 重用 | parentID 生效；已有 ID 被采用，新的 title / metadata 不覆盖原值；默认继承父 metadata / permissions | 创建前保存身份，恢复必须核对父级和已有配置 |
| 实际权限断言 | 继承 deny 的孩子被拒；显式 allow 的孩子可通过同一权限断言 | 显式权限替换继承，插件必须自行限制能力请求 |
| 未交付输入重试 | 相同 Session、类型、ID 返回第一次内容、metadata 和 delivery；跨 Session 或跨类型重用冲突 | 保存固定输入 ID；相同 ID 不用于修改载荷 |
| 并行与专属指令 | A、B 两个实际模型请求同时到达屏障；分别只含各自 MemberSpec 标记 | 通用 worker + 单 Session 持久指令方案成立 |
| 已交付输入重试 | 输入交付后重试仍返回首次载荷，Inbox 没有新增重复项 | 接受重试可在交付后继续使用同一 ID |
| 调用级消息来源 | 模型实际调用 `p0_send`；工具上下文的 Session、assistant message、call ID 写入兄弟 Inbox | 能可靠保存发送调用与目标消息的关联 |
| 压缩后的指令 | 确认 compaction 状态为 completed；摘要不含 MemberSpec，但下一次请求仍包含成员指令 | 不依赖普通对话历史保存成员规格 |
| KV 作用域与竞争 | 两个 Location 的不同插件实例共享同插件 ID 的键；两次先读 0 再写 1，结果为 1 | 实例内锁不等于存储 CAS；Team 键必须命名隔离 |
| 插件自有 SQLite | 公共插件可导入 `bun:sqlite`；两个 Location 可访问显式指定的同一实验数据库 | 当前 Bun 基线上可采用插件事务数据库 |
| 实时事件 | 收到 enqueued、delivered、execution started / succeeded；started / succeeded 的 data 只有 sessionID | 有生命周期事实，但无逐输入的完成证明 |
| 日志恢复与游标 | 重启后的新插件订阅不回放旧事件；HTTP 持久日志可读历史，返回 log.synced；按其 seq 续读无旧事件 | 恢复需日志或事实核对，不能只监听实时事件 |
| SIGKILL 后恢复 | 新进程读到旧 Session、成员指令、未交付消息及来源、KV 和插件 SQLite 数据；新请求仍带成员指令 | 基础数据可恢复，业务未决操作协议还需实现 |

实验通过 HTTP 查询 Inbox 和持久日志，插件通过 `ctx.session` 创建和投递。两者没有混称：`ctx.session` 的实测方法中没有 inbox、message.get 和 log；`ctx.app` 也不提供完整客户端所需的服务地址和凭证。生产中的完整客户端连接或等价适配尚未实现。

### 2.2 选定宿主测试：142 项通过

运行文件为 `plugin/session-instructions.test.ts`、`session-create.test.ts`、`session-prompt.test.ts`、`session-instructions.test.ts`、`agent.test.ts`、`session-run-coordinator.test.ts`。结果为 **142 pass、0 fail、497 次断言**。

这些测试补充检查了宿主自身的创建、接受、指令、Agent 和执行协调规则，其中部分使用宿主测试替身，不能将 142 项全部描述为端到端集成测试。

### 2.3 SQLite 存储探针：3 项通过

1. 两个真实 Bun 进程同时从 revision = 1 竞争同一任务，条件 UPDATE 只有一个返回 changes = 1，最终 revision = 2。
2. 一个事务中同时修改任务与插入 outbox，再主动抛错，任务和通知一起回滚。
3. 成功事务同时提交任务 revision = 3 和通知，关闭并重新打开数据库后，两项都存在。

这确认了所需事务原语在当前运行环境可用，尚不等于完整任务板通过故障验证。宿主 SIGKILL 实验也没有模拟机器断电、磁盘损坏或网络文件系统。

## 3. 已确定的实现选择

- **成员配置**：采用基础 worker + Session instruction entry，并持久保存请求规格和实际解析配置。先不动态生成大量全局 Agent 定义。
- **通信**：使用原生持久 Inbox；从真实工具上下文记录来源，保存首次投递的 input ID。accepted、delivered、显式回复和任务完成分别表示。
- **任务存储**：首版在当前 Bun 基线上采用插件自有 SQLite，将 revision 条件更新、操作记录和通知意图放进同一事务。`ctx.storage` 可用于简单配置和索引，不承担任务 CAS。
- **状态恢复**：实时事件用于更新观察；恢复用固定 ID 重试、已保存操作和可查询的宿主事实。需要历史交付状态时使用公共持久日志；客户端地址和认证适配在 P1 开始时解决。
- **权限**：根据根与用户策略合成实际规则，不能把主模型请求直接当成授予结果。权限合成算法本身尚未实现。

同一 SQLite 文件的事务支持并发访问，不代表宿主已支持多进程执行所有权。首版仍限制为一个 OpenCode 服务进程管理本地 Team。宿主操作与插件数据库之间也没有统一事务，成员创建和消息投递仍需要准备记录、稳定 ID 和恢复核对。

## 4. 尚未验证与下一步

| 项目 | 状态与下一步 |
|---|---|
| npm 发布组合 | 只测试源码公共包加载，没有测试公开 registry 发布包或打包产物；发布前固定并验证实际版本组合 |
| 完整客户端连接 | HTTP Inbox / log 端点本身通过；插件获取连接地址及认证的适配未实现，不依赖未核实的隐藏字段 |
| 热重载与跨进程运行 | 没有验证热重载时写入协调者交接或多 OpenCode 进程执行；SQLite 事务仅解决数据提交 |
| 故障窗口 | 验证了已接受数据的 SIGKILL 恢复，尚未实现或验证完整 prepare → host effect → confirm 协议 |
| 资源及权限合成 | 已确认继承和显式替换行为；实际模型、工具、skill、写入范围解析与授权仍待开发 |
| 模型协作效果 | 确定性模型只证明接口与执行链路；主模型能否产生有效分工及实际成本收益留到真实任务评估 |
| 其他运行时和平台 | 未验证 Bun 1.4.4、Node、Windows、macOS；`bun:sqlite` 方案先限定于已验证的 Bun 路径 |

下一步可以进入 P1：实现有版本的 MemberSpec、成员登记、Team 授权和投递操作记录；先明确公共客户端连接方式，再决定第一版提供多少历史状态查询。尚未开始 P1 产品代码。

## 5. 复现与证据

入口见 [README](../README.md)。可执行探针为 [run.py](../run.py)、[公共插件](../plugin/index.ts)、[宿主驱动](../worker.ts) 和 [SQLite 实验](../sqlite-probe.ts)。[机器可读摘要](../evidence/2026-11-06.json) 保存宿主基线、检查名称、关键结果与探针源文件摘要。

原始 report、宿主日志、JUnit 与临时数据库保留在本地并被 Git 忽略。它们包含完整的合成模型请求和临时路径，不需要成为后续项目的长期输入。
