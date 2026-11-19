# ReqPilot · 需求领航

ReqPilot 是面向 DeepSeek Harness 的需求驱动 AI 开发插件：L1 引导需求与有限授权，GPT/DOT 统筹独立 Codex 开发、测试和审核，L1 提交有证据的最终报告。

## 统一名称

- 工程及 GitHub 仓库：**ReqPilot**，中文名称：**需求领航**。
- 插件及 Agent 入口：**ReqPilot · 需求领航**；英文界面为 **ReqPilot**。
- 官方仓库：[Merrychen-CMY/ReqPilot](https://github.com/Merrychen-CMY/ReqPilot)。
- 图标：[harness/icon.svg](../../harness/icon.svg)，原创图形为青绿色罗盘，表达从原始需求到可验证成果的方向引导。透明背景、36×36 SVG，无外部资源、字体或脚本。

## 安装兼容

3.2.1 起采用新名称。为让现有安装可以识别为同一插件，npm 包名和运行插件 ID 继续使用 `ai-programming-workflow`，preset ID 继续使用 `ai-programming-l1`。这两个标识不是界面显示名称。

配置和任务目录仍为 `.ai-programming-workflow`，凭据引用仍为 `AI_WORKFLOW_OPENAI_API_KEY`。本次不迁移、删除或重建用户配置、凭据、任务、审批和预算记录。旧文档来源名称与历史验证报告也保留原貌。

升级使用 `reqpilot-3.2.1.tgz`，在 Harness 插件管理中替换现有安装并按宿主要求重启；Agent 选择器中显示新名称。不要为改名安装第二份不同包 ID 的副本。旧任务的版本绑定、最终报告及中断限制仍按原规则处理。

## 展示位置

插件清单从 `./icon` 读取 SVG，从 `./locale/*.json` 读取中英文名称和说明；配置页内嵌同一图形；GitHub README 引用同一 SVG。安装包必须同时保留这些导出和资源。

本次检查资源解析、打包内容和独立渲染。真实 Harness 中的显示需在升级安装后核验，不将预览截图视为宿主安装完成。
