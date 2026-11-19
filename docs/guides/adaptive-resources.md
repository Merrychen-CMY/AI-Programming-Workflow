# L1自主资源管理

3.14.0起，新任务由L1制定初始额度，并自主批准后续资源追加，不再要求用户审批资源上限。用户集中确认目标、硬约束、验收、操作范围及明确的最终截止时间。旧任务通过展示此变化的原生升级采用新规则；未升级的固定版本仍按旧记录运行。

原始输入、开工确认、已用次数、失败及API账本保持原样。resources.json追加记录每次调整的前后数值、理由、证据、剩余工作、收益和策略。提高额度不清零消耗，也不自动批准产品执行、清除未知操作或撤销最终报告。

## 参数与单位

limits在原委托中保留初始数值；l1_adjust_limits中的limits是L1当前批准的九项有限累计额度，不是“再增加几次”。计划budgets是制定时快照，实际执行按当前资源记录判断。

| 字段 | 单位与含义 |
| --- | --- |
| `implementation_attempts` | 全任务L3派工次数，初次实现和修复共用；已完成任务不会因增加额度自动重做 |
| `same_root_cause_attempts` | 同一具体根因累计失败阈值；达到当前阈值，下一次重试前进入L1评估 |
| `infrastructure_retries` | 全任务设施/证据恢复的额外重试次数，可以为0 |
| `timeout_seconds` | 每次工作会话/审核/检查的最大秒数，并受剩余总时长和截止时间约束 |
| `l1_reviews` | 需求和计划的独立审核累计次数，包括拒绝的审核 |
| `revisions` | 已初始化基线修订与事实补充共用的累计次数，可以为0；不是每个阶段重新计数 |
| `api_calls` | DOT API累计请求次数，包括已发出后失败或结果未知的请求；不含配置页的独立探测 |
| `api_max_output_tokens` | 单次DOT API输出token上限；不是全任务token总额 |
| `wall_time_seconds` | 从任务首次建立API/审核账本起的经过秒数；新流程在首次需求独立审核前开始计时，包含暂停和等待，调整不重置起点 |


总运行时长wall_time_seconds可以由L1增加；用户明确指定的expires_at最终截止日期仍需用户同意才能改变。API等待默认120秒，可先用l1_adjust_limits增加timeout_seconds，再用l1_review_api调整实际等待。模型输出规格、账户余额、供应商硬限额和宿主工具的容量限制属于外部条件，插件不能假装已经修改。

## 调整与继续

1. L1首次调用l1_adjust_limits制定额度。
2. 达到当前额度进入BUDGET_REVIEW_PENDING，DOT提交剩余工作、失败证据、累计消耗和建议。
3. L1评估是否有可行方案和明确收益，按templates/resource-allocation.example.json提交limits、reason、evidence、remaining_work、expected_benefit、strategy，自主批准追加。
4. 控制器保留历史并更新资源，纯资源变化不使需求/代码基线失效、不自动派工。涉及方案或测试要求变化时重新审核受影响内容。
5. 无可行方案时由L1明确评估后提交非成功报告，不能因一次额度不足直接失败。

误将纯limits变更交给l1_request_authorization时，新委托任务会按同一L1资源审批路径处理，不创建用户审批待办。混合资源和范围变更须分开；allowed_paths、hard_constraints、authorized_context和明确截止时间变化仍通过原生用户审批。不能以资源工具取消独立测试、真实证据要求或改变目标。

## 报告与兼容

报告列出原始额度、当前L1批准额度、每次调整理由、实际消耗和结果。计数及报告的DOT token用量不等于覆盖所有模型的金额硬限额；未知费用不得记为零。旧adaptive_limits任务没有resource_authority委托且未完成新版升级时，保留旧上限语义，不重写confirmation.json。旧最终报告不重开，真实中断按专用恢复核实。

参见[同等能力与职责分工](agent-capabilities.md)、[API评估与恢复](api-recovery.md)和[完整流程与恢复](flow-recovery.md)。
