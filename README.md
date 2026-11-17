# AI 自动编程流程规范

把双 L1、L2 统一回流调度、独立验证和受控交付整理为 Agent 可以按需读取的项目。版本 **1.0.0**，基线日期 **2026-11-17**。

核心规则：**L4/L5 → L2 接收、分析根因、分类与调度 → 对应处理层**。代码缺陷由 L2 派发 L3；需求/AC 交 L1；任务拆分/方案问题由 L2 协调；测试计划/用例交 L1-V；测试设施交 L4；交付目标/授权由 L5 整理并按权限重新审批。

## 给 Agent 的入口

先读 [AGENTS.md](AGENTS.md)，再读 [START_HERE.md](START_HERE.md)。阅读你被分配的角色和当前任务所需的文件即可。不要把所有文档一次装入上下文。

机器导航见 [INDEX.json](INDEX.json)，核心结构化策略见 [policy/workflow.json](policy/workflow.json)。GitHub 链接不能保证所有 Agent 自动读到仓库内容；给 Agent 固定提交的链接或本地检出路径，并要求它报告实际读到的文件。

## 内容结构

| 路径 | 内容 |
| --- | --- |
| `AGENTS.md` | 最短入口、操作边界和禁止事项 |
| `roles/` | L1、L1-V、L2、L3、L4、L5 的独立角色说明 |
| `policy/` | 路由、门禁、状态机、16类L4与12类L5场景、重验和48项拒绝测试 |
| `docs/` | 核心规则、架构、术语和实施指南 |
| `docs/reference/` | 完整教程的21章，保留技术内容并按章读取 |
| `templates/` | 产品基线、验证计划、项目上下文、问题与任务交接模板 |
| `examples/contracts/` | 原教程JSON/YAML示例，不能作为真实授权或证据 |
| `scripts/`、`tests/` | 无第三方Python依赖的静态检查及其单元测试 |
| `.github/workflows/` | 仓库规范检查，不是产品验收CI |

## 开始使用

1. 按 [上传指南](docs/guides/github-upload.md) 上传本目录的内容到 GitHub。
2. 让 Agent 从 [启动提示模板](templates/agent-start.md) 开始，先识别版本、角色和项目状态。
3. 按 [项目接入指南](docs/guides/adopt-project.md) 建立真实产品的上下文和双 L1 基线。
4. 按 [首次闭环](docs/guides/first-run.md) 做单任务试点，演练实现、用例、设施及交付问题回流。

## 本地检查

需要 Python 3.10 或更高版本，只使用标准库。在本仓库根目录运行：

```powershell
python scripts/validate_repo.py
python -m unittest discover -s tests -v
```

这些命令检查 JSON、内部链接、文件清单、源章节覆盖和核心策略约束。成功只代表**规范仓库静态检查通过**，不会运行产品测试、验证签名、批准任务或执行交付。48项真实拒绝测试的初始状态均为 NOT_RUN。

## 维护

按 [CONTRIBUTING.md](CONTRIBUTING.md) 同步修改规则、角色说明、参考章节和测试。发布前更新 VERSION、CHANGELOG、INDEX 及内容清单。适用文件有冲突时暂停受影响操作并报告，不能自行选一个较宽松版本继续。
