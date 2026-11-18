# 按需阅读，不要全量加载

## 默认入口

先读 [AGENTS.md](AGENTS.md)，然后只读当前工作需要的文件：

| 正在做什么 | 读什么 |
| --- | --- |
| 和用户整理原始需求 | [L1.md](L1.md)、[需求模板](templates/requirements.md) |
| 已收到批准需求，准备计划 | [DOT.md](DOT.md)、[简化策略](policy/simple-flow.json) |
| 受DOT指派做开发或验证 | 已批准任务及 [内部职责映射](policy/roles.json) 中对应角色 |

编排和结案分别读取 [执行计划](templates/execution-plan.md) 与 [最终报告](templates/final-report.md)，计划由L1批准，报告由L1核查后交用户审核。记录需求/计划/报告版本、规范commit与真实授权。用户通常不用查看内部基线、任务包或门禁JSON。

## 出现问题时再补读

| 事件 | 参考 |
| --- | --- |
| 嵌入式C开发/验证 | [规范入口](standards/embedded-c/README.md)、[规则映射](policy/embedded-c.json)及当前变化对应章节 |
| L1接通Harness和GPT | [接入指南](docs/guides/l1-harness.md) |
| 本地DOT运行 | [本地运行器](docs/guides/local-runtime.md) |
| 首次接入 | [接入指南](docs/guides/adopt-project.md)、[首次闭环](docs/guides/first-run.md) |
| 测试失败 | [L4矩阵](policy/l4-failures.json)、[问题处理](docs/guides/handle-issue.md) |
| 交付退回或失败 | [L5矩阵](policy/l5-delivery.json)、DOT的交付规则 |
| 需求/计划/候选/设施变化 | [重新验证](policy/reverification.json) |
| 重试上限、根因争议 | [升级规则](policy/escalation.json) |
| 超时、重启、运行状态未知 | [内部状态机](policy/state-machine.json) |
| 正式门禁、HIL或自动执行器接入 | 先读 [进阶范围](docs/advanced-profile.md)，再按 [INDEX](INDEX.json) 选相应章节 |

所有L4/L5发现统一回DOT的L2职责，保留根因、派工和独立重验记录。产品决策由L1在原始委托内处理，超出委托汇总受阻报告，普通内部修复不增加用户审批。

资料不足时先使用已有上下文；只有影响当前动作的关键缺口才汇总提问。恢复执行先核对真实状态，不能把Agent自己的旧总结当成唯一证据。

首次安装使用[L1原生插件指南](docs/guides/l1-plugin.md)：配置模型和密钥 → 检查环境 → 沟通原始需求 → 集中确认开工 → L1 自动推进 → 用户审核最终报告。
