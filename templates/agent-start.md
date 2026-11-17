# 给 Agent 的启动说明

请采用以下仓库中的AI自动编程流程规范：

- 规范仓库或本地路径：REPLACE_WORKFLOW_REPOSITORY
- 固定提交：REPLACE_WORKFLOW_COMMIT
- 产品仓库或本地路径：REPLACE_PRODUCT_REPOSITORY
- 本次角色：REPLACE_ASSIGNED_ROLE
- 本次任务：REPLACE_TASK
- 已有授权、需求/计划和项目状态位置：REPLACE_CONTEXT_LOCATION

先读取AGENTS.md、START_HERE.md、docs/core-rules.md、policy/workflow.json，再按INDEX.json读取本角色和当前事件所需文件，不要一次加载全部参考章节。

开始前回报：实际读过的文件、版本与commit、角色/任务边界、已存在的基线/候选/授权、缺失项和下一步。没有核实的信息不要当成事实。

所有L4/L5问题先交L2分析根因和调度。模板、示例和规范仓库检查结果均不是真实审批或产品验收。按本次明确授权执行；其他动作先准备可审阅结果并交对应负责人决定。
