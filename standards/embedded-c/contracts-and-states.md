# 内部契约如何接入DOT

L1代理批准 [需求](../../templates/requirements.md) 和 [DOT计划](../../templates/execution-plan.md)，用户只审核L1提交的 [最终报告](../../templates/final-report.md)。145项适用性和证据由DOT维护，不要求用户逐项填表。实际项目选一个权威记录；其他视图从它派生，不要求重复手填。已有等价结构可以适配；采用本包校验器时必须保持原Schema字段，不能直接往additionalProperties受限对象塞自定义字段。

| 对象 | 维护职责 | 与主流程的关系 |
| --- | --- | --- |
| 已批准requirements.md | L1与用户 | 产品目标、AC、规范版本、范围和授权的来源 |
| project-profile | DOT | 长期项目配置、源码范围、工具设备、权限、规则例外与检查入口；与workflow上下文互相引用，不重复批准 |
| dev-task | DOT，派L3执行 | 派生本次任务，关联需求ID、AC及批准来源；记录起点、允许路径、不变行为、风险和预算 |
| test-plan | DOT组织的L1-V职责 | 在实现前确定关键判据，绑定需求/AC及required_test_ids；对应计划基线，冻结后再绑定实际候选 |
| delivery-report | L4工具提供证据，DOT汇总 | final_identity对应最终候选，独立验收后形成报告；不替代交付授权 |

## 不混用的状态与身份

- DRAFT是未配置草案；READY表示契约具备约定条件，不是任意硬件操作许可。ACTIVE/CLOSED是项目、任务或计划生命周期。
- 原机器契约CLOSED仅表示该对象流程结束，不等于回流问题单CLOSED。回流问题仍须处置和独立重验通过后由DOT关闭。
- FINAL表示报告定稿；acceptance_status单独取ACCEPTED、INCOMPLETE、REJECTED。ACCEPTED不自动把工作流置为DELIVERED，仍核对动作授权及真实回执。
- PASS/FAIL/SKIP/BLOCKED/ERROR/NOT_RUN是单项结果；NOT_APPLICABLE是有理由的适用性判断，在原机器结果中对应SKIP，不能计作PASS。
- starting_source/planned_source描述起点；final_identity描述最终实际输入。不能要求实现前后哈希相等，也不能拿起点证据替代最终版本。candidate_id、issue_id、run_id和审批引用可放在主流程独立交接记录，通过document_id、artifact_id及受控记录引用关联，不修改原Schema。
- required项未执行或证据不完整为INCOMPLETE；确认不满足要求应REJECTED。可选项失败如果揭示需求不满足也不能忽略。

## 预算与授权只算一份

DOT的实现总尝试包含初次；原模板automatic_fix_attempts由项目适配明确为后续自动修复上限。例如总尝试3次，对应初次1次和后续最多2次修复，不给两套预算分别放行。test_retry_attempts仅用于已说明的暂时设施故障；修复后重验另记运行。项目、任务、用例、硬件写入和总时间限制同时满足，取实际允许的更严格边界。

模板中的零和null是未配置，不是默认次数或默认硬件授权。激活前核对计数口径与真实授权；重启、等待或部分进展都不清零。测试计划由获委托的验证职责维护，不能为通过验收删必需用例、降低风险、放宽容差或改AC。

## 校验器边界

Python 3.11+。在独立开发环境安装 [固定依赖](v1.3/machine/tools/requirements-validation.txt)，再用 [原只读工具](v1.3/machine/tools/validate_contracts.py) 校验实例。依赖仅用于流程契约，不属于固件。

```powershell
python -m pip install -r standards/embedded-c/v1.3/machine/tools/requirements-validation.txt
python standards/embedded-c/v1.3/machine/tools/validate_contracts.py --require-ready PATH_TO_TASK_JSON PATH_TO_PLAN_JSON
```

退出0只表示结构/部分状态合法；退出1表示实例错误，2表示require-ready拒绝DRAFT，3表示工具/用法错误。FINAL且INCOMPLETE的报告可能返回0，绝不能据此验收通过。Schema只从本地注册，.invalid标识不是下载地址。

在执行与验收前，DOT和实际执行器还必须按 [machine README第六节](v1.3/machine/README.txt) 检查跨文件ID、需求覆盖、实际发现/执行集合、计数、时间和尝试、真实文件哈希、最终版本、设备身份/授权/前提、预算、参数/校准及例外。原工具未实现这些检查，缺少所需核验能力时保持阻断或按项目认可方式独立核验，不能声称结构校验已覆盖。


## 华为条款ID进入现有契约

按 [逐条使用说明](v1.3/huawei/README.md) 将适用HW条款写入任务requirements和测试/审查映射。维持原Schema 1.1和规范1.3，不新增用户表单。清单采纳状态与产品执行结果分开；未判断适用性、无必要证据或规则例外未经认可时不能以目录完整或Schema通过宣称产品合规。
