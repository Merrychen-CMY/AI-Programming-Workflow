# 模板使用顺序

用户审核三份主要产物：[需求文档](requirements.md)、[DOT执行计划](execution-plan.md)、[最终报告](final-report.md)。由L1/DOT填写，用户不必填内部JSON和逐条规范适用表。启动见 [agent-start.md](agent-start.md)。

所有模板均是草案，复制到真实项目私有位置再填写；本公开规范仓库不保存真实批准、运行状态或凭证。

| 文件 | 用途 |
| --- | --- |
| [requirements.md](requirements.md) | L1的需求、约束、验收与范围，首次审核 |
| [execution-plan.md](execution-plan.md) | DOT的方案、任务、独立执行与预算，开工审核 |
| [final-report.md](final-report.md) | 成功/失败/未完成报告，结束审核 |
| [local-execution-plan.example.json](local-execution-plan.example.json) | 本地运行器JSON计划草案，须填写真实环境并审核 |
| [project-context.json](project-context.json) | DOT维护项目版本、角色和能力现状 |
| [product-spec.md](product-spec.md) | 内部产品规格与AC |
| [verification-plan.md](verification-plan.md) | 内部L1-V计划与候选绑定 |
| [task-draft.json](task-draft.json) | DOT准备的任务草案 |
| [issue-intake.json](issue-intake.json) | 问题接收，不预填根因结论 |
| [handoff.md](handoff.md) | 角色之间交接 |
| [verification-report.md](verification-report.md) | 独立观察结果与证据 |
| [delivery-request.md](delivery-request.md) | 最终报告批准后的具体交付授权申请 |

REPLACE_*、null、NOT_RUN、DRAFT表示未完成；不能写成PASS或APPROVED绕过真实检查。正式审批身份与权限条件见 [接入适配器](../docs/reference/14-adapters.md)。
