# 10 配置 govrail 正式判定

> v2.0使用范围：本章保留原完整技术参考。默认用户流程为L1整理需求、用户批准、DOT执行；本章内部角色和正式工具契约按 [进阶范围](../advanced-profile.md) 适用，不要求用户逐层操作。

govrail 运行检查，项目证据验证器理解候选与账本契约。保留 candidate_admission、evidence_gate、delivery_guard 三个自定义入口，并增加 verification_admission 校验 V0、issue_router 约束 L2 回流调度。以下 YAML 为这些适配器共同读取的自定义策略，不是 govrail 原生配置。

```yaml
schema_version: 1
project_id: REPLACE_PROJECT_ID
approval:
  gate1_action: authorize_execution
  gate4_allowed_actions: [merge, release]
  require_authenticated_owner: true
  reject_revoked: true
execution:
  max_parallel_tasks: 1
  max_attempts_per_task: 3
  same_failure_limit: 2
  task_timeout_minutes: 45
verification:
  required_checks:
    - software.unit
    - software.acceptance
    - hil.boot
    - hil.serial-ping
    - hil.invalid-command
  max_infrastructure_retries: 2
  allow_required_skips: false
  require_nonzero_test_count: true
  require_exact_candidate: true
  require_complete_attempt_history: true
  evidence_max_age_hours: 24
delivery:
  require_evidence_set_digest: true
  require_current_target_base: true
  require_exact_artifact_digest: true
  allow_rebuild_after_approval: false
reflow:
  intake_owner: L2
  direct_l4_l5_to_l3: false
  require_root_cause_record: true
  require_issue_before_retry: true
  block_unknown_root_cause: true
  close_only_after_independent_reverification: true
  implementation_change_requires_new_candidate: true
  preserve_all_attempts: true
```

重试数、45 分钟任务时限和 24 小时证据有效期是初始建议，可按项目修改并版本化。计时使用可信服务时间，存储采用 UTC，界面可显示北京时间。未知状态、非法时间、缺失日志、版本不匹配一律不放行。

门3通过必须同时满足：

1. 门1批准有效，审批对应当前开工包。

2. 候选、代码仓库、策略、验收计划和构建产物完全匹配。

3. 每个必需 check_id 与 AC 都有当前候选的完整结果。

4. 原始文件可取回，哈希、生产者身份和回执有效。

5. 必需测试无失败、无缺失、无未批准跳过；证据未过期。

6. 同一候选没有尚未处置的功能失败，且全部尝试可见。

7. 验证过程中没有残留运行者或身份未知的子任务。

输出不可覆盖的 verdict.json，记录 PASS 或 FAIL、原因码、候选哈希、L1-V 包哈希、证据集合哈希和策略版本。原因码区分 TEST_FAILED、INFRA_ERROR、EVIDENCE_MISSING、STALE、IDENTITY_MISMATCH 等；它们是分诊输入，不等于已确认根因。非 PASS 统一进入 L2 接收队列并阻止放行；L2 不能改写 verdict。

按已安装 govrail 的 schema 配置 gates.json。官方模板使用 gates、modes、command 参数数组等字段；正式配置要保留需要的现有门禁。[govrail 配置模板](https://raw.githubusercontent.com/Lixiang9716/govrail/master/gov/templates/gates.json)

下面是控制仓库中可供合并的配置片段示例，脚本名是待实现的项目适配器。命令仅在相应文件和验证逻辑已经实现后可用；默认模式不应要求本地提前拥有 CI 才能生成的正式证据。

```json
{
  "defaultMode": "local",
  "modes": {
    "local": ["candidate-admission"],
    "release": ["candidate-admission", "evidence-admission"]
  },
  "gates": [
    {
      "id": "candidate-admission",
      "label": "Candidate identity and scope",
      "command": ["python", "adapters/candidate_admission.py"],
      "timeoutMs": 60000,
      "allowFailure": false
    },
    {
      "id": "evidence-admission",
      "label": "Trusted evidence for exact candidate",
      "command": ["python", "adapters/evidence_gate.py"],
      "timeoutMs": 120000,
      "allowFailure": false
    }
  ]
}
```

适配器从控制器指定的运行上下文读取候选与证据位置；不能从 PR 正文任意下载命令并执行。在本地只运行轻量准入或自测，正式 CI 明确执行必需的 evidence-admission，且验证器内部枚举全部必需检查。

```powershell
gov run --gate evidence-admission
gov self-test
```

先故意提供一份缺失证据，确认门禁返回失败，再接入完整证据。gov self-test 只能覆盖已配置的拒绝场景，不自动证明本教程所有约束成立。官方实践也要求给项目接入产品检查并验证拒绝行为。[govrail 配置实践](https://raw.githubusercontent.com/Lixiang9716/govrail/master/docs/cookbook.md)

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
