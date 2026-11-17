# 接入真实项目

1. L1读取产品AGENTS及本规范固定commit，接收用户需求、参数和环境，形成 [需求文档](../../templates/requirements.md)。不覆盖已有保护。
2. 用户审核需求具体版本。DOT核对产品位置、真实工具和预算，按 [执行计划](../../templates/execution-plan.md) 提供独立开发/测试/审核安排，再由用户批准计划。
3. 选择 [本地运行器](local-runtime.md) 或已有正式编排系统；记录实际能力，未接通保持未实现，不声称已自动启动。运行状态/批准/私有证据放产品私有目录。
4. 从一个小任务闭环开始。成功或重试耗尽提交 [最终报告](../../templates/final-report.md) 给用户审核；成功获准也不推定外部发布/刷写授权。
5. 正式控制器、签名审批、可信证据、CI和HIL按 [适用范围](../advanced-profile.md) 补齐；本地同账户会话不替代权限隔离。

[project-context.json](../../templates/project-context.json) 由DOT维护；用户不逐项填写。嵌入式C按 [接入清单](../../standards/embedded-c/adoption.md) 确认规范版本、范围、独立判据和证据，不增加逐条用户填表。
