# AI自动编程流程：只沟通需求，只审核最终报告

**你向L1提供原始需求、参数和环境。L1生成、独立审查并批准需求与执行计划，通过GPT API调用DOT组织开发、测试、审核，最后由L1提交成功或失败报告给你审核。** 当前版本 **3.0.2**。

```mermaid
flowchart TD
 U[你：原始需求和信息] --> L[L1 DeepSeek Harness：整理需求与内部L1-V计划]
 L --> R[独立审查 → L1代理批准]
 R --> D[GPT / DOT：计划、调度、根因分析]
 D --> P[L1批准执行计划]
 P --> E[本机运行器：独立开发、测试、审核]
 E -->|问题与证据| D
 D -->|需求或验证基线问题| L
 E --> F[DOT成功／失败证据报告]
 F --> S[L1核查并提交最终报告]
 S --> H[你：审核最终报告]
```

## 从这里开始

| 文件 | 用途 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Agent共同规则与审批边界 |
| [L1.md](L1.md) | 唯一用户入口、代理审批和最终报告 |
| [DOT.md](DOT.md) | GPT/DOT调度、独立执行与回流 |
| [Harness接入指南](docs/guides/l1-harness.md) | 本地工具桥、API配置及接入步骤 |
| [首次跑通](docs/guides/first-run.md) | 从小任务验证完整流程 |
| [模板](templates/README.md) | 由L1/DOT维护的需求、计划、委托和报告 |

中间审批由L1完成，范围内变更也回L1。超出原始目标、预算或权限，或者关键事实无法确定时，形成受阻报告；不能修改用户参数、降低验收标准或无限重试。用户无需另外操作DOT或维护内部清单。

## 已实现与接入限制

仓库实现了本地MCP stdio工具桥、OpenAI Responses API计划/调度/根因分析适配、独立Codex会话、L1代理审批、候选与证据校验、累计预算和最终人工报告门禁。GPT模型需在本机显式配置，API密钥仅从桥进程环境读取，不写入仓库或发给工作会话。

这不是已安装的DeepSeek Harness插件。现成dsh-spec-collab仍保留它自己的人工Ready规则；本方案通过L1专用MCP工具使用独立的代理批准记录。插件挂载、真实API账户和完整产品任务仍需本机接通验证。接口及拒绝测试使用模拟API/工作会话；不得据此宣称真实Harness→GPT→Codex端到端已经通过。详见 [3.0验证记录](docs/reports/3.0.0-validation.md)。

本机审批记录不是受信签名服务；测试命令执行器不是OS沙箱，独立会话也不等于同账户安全隔离。现有分支/设备/部署保护照常保留。最终人工确认仍由用户在自己的终端完成；没有外部交付执行器，不自动合并、发布或刷写。

## 保留的规范与技术内容

双L1职责（Product L1与内部L1-V）、L2～L5、21章技术参考、16类L4/12类L5回流、48项正式系统拒绝场景均保留；默认中间批准主体改为L1，最终报告仍由用户审核。

嵌入式C使用 [华为项目规范V1.3](standards/embedded-c/README.md)：145父项、50子项、33条汇总规则及18章原技术内容。注释按 [Doxygen采用清单](standards/embedded-c/v1.3/doxygen/README.md) 的12项/33子项执行；冲突以华为为准。真实Doxygen检查入口保留，工具通过不代替语义审查或产品验收。

```powershell
python scripts/validate_repo.py
python -m unittest discover -s tests -v
```

以上验证仓库及运行器，不证明产品/HIL合规。版本历史见 [CHANGELOG](CHANGELOG.md)，按需导航见 [START_HERE](START_HERE.md)。

编程规范新增[通用编程与交付约束](standards/embedded-c/v1.3/project-constraints/README.md)：完整追踪用户材料U01～U26，并将适用范围推广至新项目的自有C/H。华为优先，Doxygen保留；规范摘要及独立审查接入运行器，源码语义仍需实际验证。

本次约束整合的检查范围见[3.0.1验证记录](docs/reports/3.0.1-validation.md)。

L1与DOT默认采用[合作沟通约束](docs/guides/l1-dot-communication.md)：信息足够、事实有据、聚焦任务、表达清楚。已接入现有API和L1工具桥提示，不增加工具或审批回合；尚未量化效率收益。
