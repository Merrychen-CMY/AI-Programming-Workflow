# 按需阅读导航

## 所有 Agent 必读

[AGENTS.md](AGENTS.md) → [核心规则](docs/core-rules.md) → [workflow.json](policy/workflow.json) → 分配给你的角色文件。阅读规则不会自动给你分配角色或任务。

## 按角色继续

| 角色 | 阅读入口 | 本次工作的产出 |
| --- | --- | --- |
| L1 | [产品需求](roles/product-l1.md) | 产品基线、AC、变更决定 |
| L1-V | [验证基线](roles/verification-l1v.md) | 验收计划、用例治理、候选验证包 |
| L2 | [调度](roles/orchestrator-l2.md) | 任务图、根因分析、处理任务及回流记录 |
| L3 | [实现](roles/implementer-l3.md) | 授权代码、自测和候选提交 |
| L4 | [独立验证](roles/verifier-l4.md) | 实际验证结果、环境身份和原始证据 |
| L5 | [交付](roles/delivery-l5.md) | 验收汇总、风险评估、交付申请和回执 |

## 按事件补读

| 事件 | 文件 |
| --- | --- |
| 首次接入项目 | [接入指南](docs/guides/adopt-project.md)、[首次闭环](docs/guides/first-run.md) |
| L4测试失败 | [L4矩阵](policy/l4-failures.json)、[回流操作](docs/guides/handle-issue.md) |
| L5退回或交付失败 | [L5矩阵](policy/l5-delivery.json)、[交付章节](docs/reference/12-delivery.md) |
| 需求/计划/候选/设施变化 | [重验规则](policy/reverification.json)、[双L1](docs/reference/17-dual-l1.md) |
| 超预算、同因失败、根因争议 | [升级规则](policy/escalation.json) |
| 超时、重启或状态未知 | [状态机](policy/state-machine.json)、[完整状态说明](docs/reference/13-state-machine.md) |
| 接入ESP32/HIL | [HIL技术配置](docs/reference/09-esp32-hil.md) |
| 接入门禁/CI/证据 | [适配器](docs/reference/14-adapters.md)、[证据](docs/reference/08-software-evidence.md)、[govrail](docs/reference/10-govrail.md)、[CI](docs/reference/11-ci-protection.md) |
| 准备启用自动化权限 | [拒绝测试清单](policy/rejection-tests.json)、[完整上线验收](docs/reference/21-acceptance.md) |

## 上下文不足时

先列出已有对象和缺失对象。当前角色、目标仓库或AC缺失时，不猜测任务范围；没有真实授权时准备草案，不执行依赖该授权的动作。Agent重启后从控制器/项目状态恢复，不把自己的上次摘要视为唯一事实来源。

所有路径及完整章节覆盖见 [INDEX.json](INDEX.json)。
