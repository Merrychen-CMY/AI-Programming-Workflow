# DeepSeek Harness 原生 L1 插件

本插件把配置、环境检查、需求引导、一次开工确认、DOT 工具和最终报告审核放到同一个 L1 会话。用户先沟通原始需求并集中确认开工边界，之后由 L1 自动审核和推进，结束时审核最终报告。

## 安装和首次使用

1. 获取本仓库 3.1.0 安装包，或完整仓库目录。保留 `package.json`、`harness/` 以及运行器、策略、模板、规范目录，不要只复制一份提示词。
2. 在 DeepSeek Harness 的插件管理中添加本地插件包/目录。也可让 Harness 自身有插件管理能力的 Agent 使用 `plugin_manager install_bundle` 安装本地来源；实际参数以宿主工具声明为准。本仓库不直接修改你的 Harness 配置文件。
3. 创建新会话，选择 **AI 编程 L1**。安装包添加独立 preset，不替换默认 Agent，也不修改 dsh-spec-collab 的 Ready 规则。旧会话可能仍保留旧 preset，不能用来判断新插件是否生效。
4. 对 L1 说：**“开始一个项目，请先引导我配置 GPT/DOT、检查环境，再集中确认开工。”** L1 会提供本机配置页链接。在页面填写 GPT API 模型 ID、密钥及 Python/Codex 可执行文件路径。这里的 GPT 就是 DOT，不是另一个人工操作入口；L1 自己的模型仍由 Harness 设置。
5. 点击保存，再点击“测试 API”。测试发出两次可能计费的固定示例请求，核对工具调用和结构化输出兼容性，不发送项目内容。不在安装、打开页面或启动聊天时自动调用付费 API。
6. 回到 L1，提供独立的产品目录、原始需求及已有资料。L1 根据九项话题补齐信息并检查环境。新项目目录至少有一份真实输入文件，例如原始需求；不要拿插件目录当产品目录。
7. 阅读原生提问中完整的开工摘要，确认模型、需求、基线、范围、AC、环境、测试、交付、安全、允许路径、预算和有效期，然后选择 **“确认开工并委托 L1 自动推进”**。不需要再手填需求/委托 JSON。
8. L1 调用 DOT，独立审核并批准需求和计划，自动运行。最后在同一会话展示 DOT 原始证据报告及 L1 结论，并让你选择 **“已审核此报告”**。失败/UNKNOWN 的确认不会授权重试；成功确认也不执行外部交付。

本版核对的宿主是本机 **DeepSeek Harness 0.2.2-rc.3**，构建提交 `6b729fedde0c8fd03af7afdf5a711aa64fe87c3b`。其他版本需要重新核对扩展接口。安装方式依据宿主随包的 dsh-agent-preset、plugin-manager、dsh-tools、dsh-user-questions、dsh-credentials 说明与源码；上游参考：[项目仓库](https://github.com/deepseek-ai/deepseek-harness)、[插件管理说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/boot/plugin-manager/README.md)。兼容性核对和本地自检不等于已在你的真实 L1 会话中安装或跑通产品任务。

## 环境检查的含义

必需基础条件包括 Python 3.11+、Git、Codex CLI、Codex 登录及模型 API 兼容性。L1 还须把具体编译器、依赖目录、测试入口和设备条件列入草案。路径存在只证明存在；设备等无法自动探测的条件使用 `USER_ATTESTED`，展示用户提供的证据。没有证据保持 UNKNOWN，必需项 FAIL/UNKNOWN 阻止开工。

实际构建、测试、Doxygen、独立审核与适用 HIL 仍在批准后的运行器中产生证据。插件不把环境检查当作产品验收，不自动安装软件、关闭安全机制或修改设备。

## 状态与恢复

```mermaid
flowchart TD
 D[DISCOVERY：配置与需求沟通] --> P[PREPARED：环境检查与项目指纹]
 P --> U[用户集中确认完整开工摘要]
 U --> C[START_CONFIRMED：固定委托与预算]
 C --> R[DOT提案 → 初始化 → L1独立审查及代理批准]
 R --> E[既有运行器执行与回流]
 E --> F[L1提交最终报告 → 用户审核]
 P -->|必需项失败或未知| D
 U -->|取消、跳过或内容变化| D
 E -->|中断| X[UNKNOWN：保留记录、禁止自动重放]
```

前三个状态属于开工引导，不替代既有 REVIEW_PENDING、PLAN_PENDING、EXECUTING、VERIFYING 等运行状态。每个 Harness 会话固定一个任务。记录在用户目录 `.ai-programming-workflow/tasks/<会话摘要>/`，与产品和插件代码分离；重启会话沿用记录，不能重置累计消耗。新目标应开启新会话、再次集中确认，并在基线字段关联旧报告，不能通过换会话绕过失败或预算。

工具在可能修改状态的操作之前记录 UNKNOWN 标记，收到完整结果才清除。宿主退出、取消、进程失败或结果不明会保留标记；只能查状态或提交停止/最终报告，不自动重放。终止桥进程不证明其所有后代进程已终止，需要核实旧进程和证据。保守拒绝也可能要求结束本任务，再以明确关联旧记录的新任务处理；没有自动“清除失败”按钮。

开工后不能静默切换模型、Python/Codex 路径、范围或预算。可以在原模型下更换凭据并重新测试 API。用户数据和项目文件变化须按基线规则处理。配置页是会话内临时本机服务，关闭或卸载插件使链接失效；不用于远程服务器上的浏览器配置。

最终报告超过原生界面 65,000 字符上限时，插件拒绝截断批准。L1 应提供完整报告文件，用户按[工具桥指南](l1-harness.md)使用原有终端报告审核；不得改用 L1 自动确认或隐去证据。

## 密钥、权限和已有规范

密钥由用户在本机密码输入框填写，经宿主 `credentials` 服务保存。普通设置文件只含模型/路径/探测状态和凭据指纹，不含密钥。页面仅监听 127.0.0.1，使用临时会话令牌、Host/Origin 检查，不回显密钥、不在错误中输出响应正文。凭据服务具体存储方式由宿主决定，本插件没有额外的操作系统密钥库保证。

执行时仅 GPT/DOT 桥子进程临时获得 `OPENAI_API_KEY`，不进入模型工具参数、命令行、仓库或 Codex/测试环境。当前仅支持官方 Responses 端点；没有自定义代理地址、自动选择模型、自动充值或自动创建密钥功能。OpenAI 的密钥保密要求见[官方 API 概览](https://developers.openai.com/api/reference/overview)。

开工摘要说明数据将发送到配置的模型服务，用户须确认实际需求中的敏感数据边界。原始输入、候选、证据仍可能包含业务信息，请保护本机记录。插件不是操作系统沙箱；同账户进程可访问的本地文件不能提供强隔离或签名审批保证。

华为编程规范、Doxygen、通用约束、格赖斯合作原则和 L1 表达规则仍通过原有入口/策略及运行器执行。L4/L5问题仍先回DOT/L2，按根因调度；插件不改变职责、不降低AC、不恢复逐份人工审批。

## 维护和验证

插件入口为 `harness/index.js`，打包根为仓库根。`harness/cordis.patch.yml` 声明专用 L1 preset。`workflow_reference` 提供受限只读规则读取；内部 Python 管道不直接暴露给模型。开工和最终确认来自 `ctx.userQuestions` 的原生人类答复，模型无法传入 `confirmed=true`。

运行 `node --test harness/tests/*.test.js`、`python -m unittest discover -s tests -v` 与 `python scripts/validate_repo.py`。打包用 `python scripts/package_harness.py --output <仓库外目录>/ai-programming-workflow-3.1.0.tgz`，只收录校验通过的MANIFEST文件，避免目录打包夹带Python缓存；无安装脚本，无额外第三方运行依赖，宿主提供 DSH 服务接口。禁止把本机密钥、配置、运行记录或缓存打包提交。验证边界见[3.1.0验证记录](../reports/3.1.0-validation.md)。
