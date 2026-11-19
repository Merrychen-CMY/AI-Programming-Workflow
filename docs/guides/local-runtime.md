# 本地运行器

默认从 [L1 Harness工具桥](l1-harness.md) 使用：L1生成需求，代理批准需求/计划，GPT/DOT调度独立Codex开发、测试和审核，L1提交最终报告，用户只审核报告。启动入口是`scripts/l1_bridge.py`。

`scripts/dot.py`继续保留2.x人工终端模式用于兼容旧运行。用旧CLI直接`init`创建的是人工审批运行，要求人工批准需求/计划；不要把该兼容入口当作新的默认流程。工具桥创建并固定`delegated_l1`运行，中间批准通过L1工具完成；旧CLI对该模式只接受最终报告人工批准，不能代替L1审批。

## 执行和证据

运行目录和需求/计划在产品之外；独立工作会话不能在自己的产品工作区直接修改批准对象。固定原始项目快照、需求/计划/华为/Doxygen基线；每次实现后核对allowed_paths，候选不可变。测试/审核与开发分别使用新的会话。

`verification.test_command`使用真实argv数组，控制器直接执行并保存输出；它不是OS沙箱。测试入口及其代码必须经过独立审查，不能夹带发布、设备写入或删除动作。独立测试Codex仍须实际运行检查，零测试不通过。

`huawei-c`必须配置实际Doxygen路径、精确版本和全部适用自有C/H源码范围；见 [Doxygen指南](doxygen-checks.md)。真实文档检查在候选副本中执行，失败统一交DOT分析；工具通过仍不证明语义或全部华为条款已验证。

## 状态及故障

REVIEW_PENDING/PLAN_PENDING由L1审查批准；DECISION_PENDING回L1处理范围内基线变更。缺事实、超委托、次数耗尽或尚未查明原因时，L2先分析方案并交L1协调，不自动结案。UNKNOWN保留操作保护，不重复调度旧操作。

每次外部派发前保存active_run；若进程崩溃，保留未决状态并暂停，L2调查、L1评估恢复方案。旧CLI的`reconcile`仍供维护者在实际核实旧进程/副作用后使用，不作为默认用户中间审批节点；只有L1明确评估恢复方案不可行后，才可提交包含完整评估的非成功报告。

需求/计划变更会清空旧批准，保留尝试、失败和审查历史；新候选必须重验。失效批准、候选被改、证据被改均阻断执行。最终报告由L1提交后，用户可在自己的终端运行：

```powershell
python scripts/dot.py status --run-dir ABSOLUTE_RUN_DIR
python scripts/dot.py approve report --run-dir ABSOLUTE_RUN_DIR
```

报告失败被确认后不自动重启。新的用户任务可明确关联旧运行及未完成事项，不能通过复制目录、删除ledger或重置累计数继续原失败任务。

本地同账户文件不是受保护签名服务；受保护分支、CI、HIL/凭据边界见 [正式范围](../advanced-profile.md)。验证事实见 [3.0记录](../reports/3.0.0-validation.md)，历史记录保留原时点结论。
