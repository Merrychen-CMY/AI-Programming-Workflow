# 模板使用顺序

默认只给用户审核 [requirements.md](requirements.md)。启动见 [agent-start.md](agent-start.md)。其他模板供DOT和内部执行者按需维护，不要求用户逐一填写。

## 模板使用说明

本目录中的对象均为草案。复制到真实项目约定的位置再填写，不在通用规范库里积累运行状态。

| 文件 | 用途 |
| --- | --- |
| [agent-start.md](agent-start.md) | 给Agent的首次读取与任务说明 |
| [project-context.json](project-context.json) | 项目、版本、角色及能力现状 |
| [product-spec.md](product-spec.md) | Product L1需求与AC |
| [verification-plan.md](verification-plan.md) | L1-V验证计划与候选绑定 |
| [task-draft.json](task-draft.json) | L2准备的任务草案 |
| [issue-intake.json](issue-intake.json) | L4/L5问题接收，不预填根因结论 |
| [handoff.md](handoff.md) | 层级/Agent之间交接 |
| [verification-report.md](verification-report.md) | L4观察结果与证据引用 |
| [delivery-request.md](delivery-request.md) | L5验收汇总与交付申请 |

任何REPLACE_*、null、NOT_RUN、DRAFT均表示未完成。不得替换成PASS或批准状态来绕过实际检查。正式契约还应根据受信服务实现进行结构校验和身份验证，参见 [接入适配器](../docs/reference/14-adapters.md)。
