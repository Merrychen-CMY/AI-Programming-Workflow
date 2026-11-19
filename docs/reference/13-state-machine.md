# 13 配置状态机与恢复规则

> 3.0适用范围：L1是唯一用户入口，在原始委托内代理批准需求、计划和变更，用户只审核L1最终报告。原技术契约保留，正式保护仍适用。见 [当前规则](../../AGENTS.md)；历史人工Ready/签名服务示例不自动授予L1身份或权限。

控制器分别保存项目阶段 phase、检查结果 outcome 和问题状态 issue_status。FAIL/ERROR 是一次观察结果，BLOCKED/UNKNOWN/STALE 是阻断状态，均不等于已确认根因。所有状态转换须带 issue_id 或批准回执、操作者身份与版本条件；下表是阶段主线。

| 阶段或状态 | 允许转换及责任人 | 进入下一步的条件 |
| --- | --- | --- |
| READY | 需求审核有效 → L2 PLANNED → L1审核执行计划 | 需求、AC、起点、计划与预算绑定；两项审核均通过才派实现 |
| PLANNED / IMPLEMENTING | L1代理批准执行计划后L2派L3；完成后 → CANDIDATE_FROZEN | 两项批准仍有效；依赖就绪；唯一写入者；任务与授权范围一致 |
| CANDIDATE_FROZEN | 门2通过 → VERIFICATION_READY | 受控构建与不可变清单；L1-V 候选包完成授权及 V0 |
| VERIFICATION_READY / VERIFYING | L2 调度 L4；完整证据经门3 → ACCEPTANCE_READY | 任何失败先进入 TRIAGE_PENDING；不得跳过必需检查 |
| TRIAGE_PENDING / TRIAGING | L2 接收、根因分析 → RESOLVING 或 BLOCKED | 不得仅凭错误码直接派修；分类和依据入库 |
| RESOLVING | 责任层完成 → REVERIFY_PENDING | L2 核对基线、候选、依赖和权限；L3 完成不等于关闭 |
| REVERIFY_PENDING | 按20.6进入 L4 重验或审批/守卫核验 | 独立核验处理结果；需重验时生成新 run_id，更新门3并交 L5 |
| ACCEPTANCE_READY | L5汇总 → REPORT_PENDING | 成功需独立测试和审核；失败/耗尽也提交报告；AC、风险、问题历史及证据齐备 |
| REPORT_PENDING | 用户批准成功报告 → DELIVERY_PENDING；失败/未完成/未知确认 → BLOCKED | 报告批准绑定候选和证据；失败确认不授权继续 |
| DELIVERY_PENDING | 成功报告已审核且有效门4及守卫 → DELIVERING | 报告审核和具体交付授权均必需；本地运行器未实现交付 |
| DELIVERING | 核对实际结果 → DELIVERED | 领取一次性执行权；超时进入 UNKNOWN，先查询真实目标 |
| UNKNOWN | L2 调度只读核对 → 原阶段或 BLOCKED | 确认旧运行/写入者/设备会话后才可创建新尝试 |
| STALE / BLOCKED | L2 记录失效原因或阻断原因 → 对应恢复阶段 | 新基线/批准或诊断齐备；不得直接跳为 PASS |
| CANCELLED | 停止任务、释放资源；保留历史 | 恢复须重新校验授权和运行身份；不能清零计数 |
| DELIVERED | L5 保存回执；后续问题新建关联问题单交 L2 | 不回写旧验收；回退只执行预先批准且仍有效的动作 |

问题状态为 OPEN → TRIAGING → ASSIGNED → RESOLVED_PENDING_VERIFICATION → REVERIFYING → CLOSED。重验失败回 TRIAGING；无法归因或等待决策进入 BLOCKED，决策后回 TRIAGING。产品与验证缺陷的关闭需 L4 独立重验及受信门禁；纯审批/目标问题需审批服务与 delivery_guard 核验，L5 复核，L2 才能关闭。L2 关闭问题不改变历史 FAIL。

实现尝试、基础设施重试、交付执行尝试分别持久化计数；每类预算独立，不能相互挪用。实现初次在内最多 3 次，同根因失败累计达到 2 次即暂停自动修复；基础设施初次之外最多 2 次重试。撤销门1禁止新派发并停止受影响任务；证据保留。功能失败不得盲目重跑到绿，偶发测试须预先批准统计规则并保留全部样本。

门3依据处理决定和独立重验证据判断原失败是否已经处置，不要求 L2 先将 issue_status 写为 CLOSED；门3通过后，L2再完成关闭记录，避免“先关单才能过门禁、先过门禁才能关单”的循环。重启恢复按控制器记录和认证身份执行；UNKNOWN 不允许并发新写入，审批过期、对象哈希变化或权限撤销均阻断受影响阶段。历史事实不可覆盖。

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)

默认原生插件补充：规划缺资料使用NEEDS_INFO子状态，不直接终结任务。未初始化显示NEEDS_INFO；初始化后附在PLAN_PENDING/DECISION_PENDING。完整request_clarification回执允许L1追加事实后重新规划，预算和授权不重置；UNKNOWN、明确终止和最终报告保持原限制。状态及恢复入口见[同任务澄清指南](../guides/clarifications.md)。
