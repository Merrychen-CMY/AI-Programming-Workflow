# L1 DeepSeek Harness → GPT/DOT接入

版本3.5.0（ReqPilot）：用户只与L1沟通原始需求、审核最终报告。以下配置由L1或安装维护者准备一次，运行时用户不手动搬运需求/计划。

推荐优先使用[L1原生插件](l1-plugin.md)，它自动生成本文中的配置/委托并提供开工和最终报告确认界面。本文保留给手动MCP集成与终端后备路径。原生插件按“配置模型和密钥 → 检查环境 → 沟通原始需求 → 集中确认开工 → L1 自动推进 → 用户审核最终报告”引导；基础环境检查无需需求草案。之后不恢复逐项人工审批。

## 已有能力及插件边界

本仓库提供 `scripts/l1_bridge.py`，是本机MCP stdio服务，使用Python标准库。L1通过它调用OpenAI Responses API，GPT/DOT生成计划、提出调度及根因判断；本机控制器执行固定门禁和独立Codex会话。GPT没有任意shell、审批或发布工具。DOT调用一次受控执行工具后，结果以控制器记录为准。

[dsh-spec-collab 0.4.1](https://github.com/zx490336534/dsh-spec-collab)仍要求其自己的人工版本确认，AI不能把需求标为Ready。本仓库没有修改该插件，也没有把自动批准伪装成人工确认。手动MCP集成时在DeepSeek Harness的L1专用AgentPreset挂载本工具桥和本文档，以本仓库的代理批准记录作为执行基线；插件可用作草稿协作，但不要依赖其人工Ready按钮触发自动执行。若必须沿用原Ready状态，需另行扩展插件的身份/状态规则，当前未实现。

## 一次性准备

1. 克隆并固定本仓库commit；准备可运行的Python3.11+、Codex及真实项目测试入口。Codex须自行完成登录；独立会话使用其现有模型设置。
2. 把产品项目、原始输入文件、委托文件、控制目录分开放置。控制目录放项目之外；原始输入与委托不能放产品或控制目录内。建议结构如下，不要提交私有配置和运行证据。

```text
task/
  product/                 产品工作区
  inputs/raw-input.md      原始用户信息
  inputs/delegation.json   固定委托
  local/l1-bridge.json     本机设置
  control/                 工具桥自动建立的计划、审批、证据与报告
```

3. L1根据原始沟通填写 [委托模板](../../templates/l1-delegation.example.json)：原始消息依据、raw-input.md的SHA-256、明确硬约束、允许路径、有效期及上限。`external_delivery`必须为false。例值只是建议，不是自动授权；L1不能把用户未提供的关键参数当作事实。
4. 填写 [本机设置模板](../../templates/l1-bridge.example.json)：所有路径用绝对路径，`model`写账户实际可用且选定的GPT API模型，`environment_context`记录真实工具/版本、测试入口、Doxygen路径和源码范围。没有默认模型替你作选择。
5. 在启动桥的本机进程环境配置`OPENAI_API_KEY`。不要发到聊天、放入仓库或写进提示。桥不把该变量传给Codex及项目测试子进程；Codex需要自己的登录或单独凭据。其他本机凭据/同账户访问仍由运行环境管理。
6. 在Harness的MCP服务配置中注册下列进程，将服务只挂载到L1的AgentPreset。字段映射以安装版本的MCP设置界面为准；此段是标准进程启动信息，不冒充可直接覆盖所有Harness版本的配置文件。

```json
{
  "name": "l1-dot",
  "command": "ABSOLUTE_PYTHON_EXECUTABLE",
  "args": ["ABSOLUTE_WORKFLOW/scripts/l1_bridge.py", "--config", "ABSOLUTE_TASK/local/l1-bridge.json"]
}
```

Harness加载的文档至少包含AGENTS.md、L1.md及本文；按需读取DOT.md、规范和模板。若通过插件的`defaultContext.documentPaths`引用这些文件，`mcpServers`也必须已在AgentPreset实际挂载，写一个名称不会自动安装服务。

服务使用MCP协议2025-03-26的stdio `initialize`、`tools/list`、`tools/call`和`ping`。工具调用是同步的；运行调用可能持续多个任务超时，客户端工具超时需覆盖委托总时长。当前没有异步任务/取消协议实现；客户端超时不代表子过程停止，必须查询已有状态，不能重建控制目录重跑。

## L1自动调用顺序

| 工具 | L1应做什么 | 结果 |
| --- | --- | --- |
| `l1_adjust_limits` | 新动态任务先分配当前额度，之后可在用户上限内调整并记录理由 | 无API调用、无派工 |
| `l1_request_authorization` | 提交具体授权变更，由原生workflow_review_authorization取得人类决定；MCP没有模型代签入口 | AUTHORIZATION_PENDING |
| `dot_plan` | 传需求全文，GPT结合固定环境生成执行草案 | 真实配置缺口时停止；不批准、不执行 |
| `dot_initialize` | 固定项目起点和委托 | REVIEW_PENDING |
| `l1_approve` | kind=requirements，传L1实际会话标识 | 独立Codex审查通过才记录L1批准；PLAN_PENDING |
| `l1_approve` | kind=plan，同样核查计划 | 独立审查通过后EXECUTING |
| `dot_run` | 请求GPT/DOT受控派工 | 独立实现、检查、测试、审核；问题统一回L2 |
| `dot_status` | 读取实际状态、审查记录、decision或最终报告 | 不会自动派新任务 |
| `dot_plan` / `l1_revise` | DECISION_PENDING时，L1修需求或内部L1-V计划；先生成新计划，再提交requirements和plan_json | 保留计数、清除旧批准，回REVIEW_PENDING |
| `l1_stop` | 评估后无可行方案、授权未获批准且无法继续，提供原因 | FAILED或UNKNOWN报告 |
| `l1_submit_report` | 阅读DOT结果后传summary和实际l1_session_id | 绑定L1说明及原结果，供用户最终审核 |

L1不得把DOT的FAILED/INCOMPLETE/UNKNOWN改成SUCCESS。委托过期仍可读取报告；动态任务可申请具体延期，批准并调整后才可继续，不能篡改原有效期。不可恢复的计划生成错误或明确停止时生成`control/preflight-report.json`，由L1直接呈现用户；此时没有创建产品运行，也没有任何实现授权。

原始输入、委托或模型配置改变不能在旧控制目录悄悄重启。需求/计划变化用revision入口，审查与修复历史保留。模型语义审核不是数学证明，原始约束须有可验证AC和独立证据支持。

原生插件使用同名DOT工具，但l1_session_id由宿主注入，模型不能自行填写。

## 最终报告与人工确认

L1向用户提供`control/run/final-report.json`及可读摘要、证据和产物。用户只审核这份最终结论；控制器不接受Agent代输入最终确认。

```powershell
python scripts/dot.py approve report --run-dir ABSOLUTE_TASK/control/run
```

原生插件可以用workflow_review_report在同一会话审核报告；MCP手动路径的用户在自己的终端读报告、输入指定摘要。成功进入DELIVERY_READY，失败确认后BLOCKED；没有部署执行。用户阅读已完成报告的时间不计入执行超时，但仍核对原始输入、候选与证据未被更改，不能借此启动新执行。用户不愿确认可用`reject report --reason ...`退回；L1不能自动恢复实现。初始计划前的失败报告由L1直接提交用户审核，无成功/交付状态。

## 预算与实际限制

按委托限制实现/同因/设施尝试、L1审查次数、修订次数、API调用次数/单次输出和总时长。API调用在发送前计数，网络失败不自动重试；重启不清零。输入大小另有250 KB上限，过大任务要拆解，不能截断成假完整需求。

API日志保存请求、响应、实际usage和调度结果。调用次数与输出上限不是人民币/美元硬限额；若需要账户费用硬上限，还须配置账户侧预算，不能称本运行器已经实现货币计费门禁。语义审查和本地记录不构成受保护审批服务。实际测试命令直接执行，需要已有项目隔离；无人值守正式环境应在独立账户/容器或受保护执行器中部署。

本版没有宣称真实API、Harness插件挂载和完整多会话产品任务已联调。验证范围见 [验证记录](../reports/3.0.0-validation.md)。API实现依据 [官方Function calling](https://developers.openai.com/api/docs/guides/function-calling)，传输依据 [MCP stdio](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)。

## 沟通约束

初始化指令与GPT开发者提示已接入[合作沟通约束](l1-dot-communication.md)。无需新工具、表单或确认回合。说明放入已有字符串字段，不扩展JSON结构；无状态API保持必要完整输入，未实现自动增量/摘要传输。规则变化按既有基线流程重审。

L1 AgentPreset同时读取[L1用户沟通指南](l1-user-expression.md)。桥的initialize指令以及独立需求/计划审查提示已包含L1-EXPR-01～05；在首次需求沟通前加载L1.md和此指南，不等到工具初始化才应用。手动工具桥接入更新时需要重新加载资料和桥；原生插件3.4.1起使用内置更新器；3.5.0支持原生确认后迁移已开工任务，未批准迁移时仍固定原版本。两种接入均不能静默迁移已开工任务或更改其批准/候选。当前接入为仓库内选择性约束，无需另行安装上游插件；不要叠加加载原始anti-defensive-writing Skill或精简提示词。尚未完成真实Harness对话效果验证。

## 同一委托内补充事实

工具桥新增l1_add_facts(facts_json,l1_session_id)，原生插件的会话ID由宿主注入，模型不能填写。dot_plan返回NEEDS_INFO时按[澄清指南](clarifications.md)核实、追加事实并重新规划；MCP本机记录仍非强身份鉴权。

动态新任务的调用顺序、额度字段与明确限制见[资源管理指南](adaptive-resources.md)。l1_add_facts不能改期限，但独立的原生追加授权可批准具体延期；两者不是同一入口。旧MCP桥没有人类追加批准界面，不接受模型填写“已获批准”。

已开工任务需要升级时，使用[原对话任务升级](task-upgrades.md)：upgrade_preview预检→upgrade原生确认→重新独立审核→DOT继续与完整验证。不得改写原开工确认或清零历史。

原生ABI4入口另提供[未提交初始化恢复](initialization-recovery.md)；模型仅能请求预览/原生审核，手工MCP桥没有可伪造的用户批准字段。
