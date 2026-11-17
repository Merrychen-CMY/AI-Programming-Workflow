# 17 双 L1 基线与候选验证授权

## 17.1 为什么需要双 L1

Product L1 决定产品要实现什么以及验收目标；Verification/Delivery L1-V 决定如何独立证明产品满足这些目标，形成验证需求、计划、用例、证据及申请交付所需材料的基线。L1-V 不重新定义产品功能、不降低产品 AC，也不授予最终交付权限。L2 负责两条基线与实现任务之间的依赖和影响协调。

L4 不凭 L3 的完成声明启动。L2 协调候选冻结和门2；L1-V 生成候选验证包并按策略取得授权；V0 校验后 L2 才调度 L4。L5 可以在验证期间查看进度和风险，但只有门3通过且材料齐备，才能提交交付申请；发现问题仍须先报 L2。

## 17.2 两类 L1 的职责边界

| 基线 | 决定内容 | 维护与批准 | 变更边界 |
| --- | --- | --- | --- |
| Product L1 | 目标、非目标、功能、产品 AC、性能、安全、可靠性与约束 | L1维护；需求负责人或用户决策 | 任何产品需求/AC 变化先由 L2 提交 L1；版本化后重新校验开工授权 |
| L1-V 计划基线 | AC 映射、用例与预期、阈值、覆盖、环境、证据、风险及预算 | L1-V维护；独立授权负责人批准 | 测试语义与计划修订由 L2 提交 L1-V；不得违反产品 AC |
| L1-V 候选实例 | 将批准计划绑定确切候选、产物、控制版本、检查清单和资源 | L1-V 导出；V0 校验有效人工或预授权回执 | 候选变更重新生成包；不得拿旧候选包开启新验证 |

## 17.3 L1-V 的生成时机与版本绑定

开工前由 L1-V 提供预设计划基线，使门1可检查验证可行性与预算；门2后再生成绑定真实候选的验证实例。常规修复后，若产品/计划/权限/预算未变，可按明确的预授权规则生成新候选实例；控制器仍须校验计划版本、对象哈希、授权范围和剩余预算。没有这项预授权时，新实例须由授权人批准。

L1-V 应至少绑定以下对象：

- product_request_id、product_spec_commit、Product AC 列表及其哈希。

- candidate_id、candidate_commit、candidate_tree、candidate_manifest_sha256 和所有交付产物哈希。

- control_commit、acceptance_plan_sha256、验证器版本及策略哈希。

- 必需检查清单、每项检查对应的 AC、预期测试数量、覆盖目标、允许的跳过规则（默认不允许）。

- 硬件型号/版本、治具、设备身份要求、固件烧录集合、测试环境与时间预算。

- 允许的基础设施重试次数、功能失败处理规则、证据保留要求和有效期。

- 验证完成后可申请的动作类型（例如仅 merge、release 或指定设备范围的 deployment）；动作不能相互推定。

## 17.4 L1-V 自定义任务包示例

以下是项目自定义候选验证包示例。序列化约定统一为保存后原始 UTF-8 字节的 SHA-256；由导出适配器生成、审批服务记录批准或预授权依据。不得在不同环节混用“规范化 JSON”和“原始字节”算法。

```json
{
  "schema_version": 1,
  "verification_request_id": "VREQ-001",
  "product_request_id": "REQ-001",
  "product_spec_commit": "REPLACE_PRODUCT_SPEC_COMMIT",
  "product_ac_ids": ["AC-01", "AC-02", "AC-03"],
  "candidate": {
    "candidate_id": "CAND-001",
    "candidate_commit": "REPLACE_CANDIDATE_COMMIT",
    "candidate_manifest_sha256": "REPLACE_CANDIDATE_MANIFEST_SHA256"
  },
  "control_commit": "REPLACE_APPROVED_CONTROL_COMMIT",
  "acceptance_plan_sha256": "REPLACE_PLAN_SHA256",
  "required_checks": [
    "software.unit",
    "software.acceptance",
    "quality.static-analysis",
    "hil.boot",
    "hil.serial-ping",
    "hil.invalid-command"
  ],
  "coverage_targets": {
    "requirements": "100% of applicable AC mapped",
    "branch": "REPLACE_PROJECT_THRESHOLD",
    "boundary_and_error_paths": "REPLACE_PROJECT_RULE"
  },
  "hardware": {
    "board": "esp32-s3",
    "hardware_revision": "REPLACE_REVISION",
    "fixture": "REPLACE_FIXTURE_ID"
  },
  "budget": {
    "max_infrastructure_retries": 2,
    "max_hil_minutes": 30
  },
  "delivery_actions_requested": ["merge"],
  "verification_policy_sha256": "REPLACE_POLICY_SHA256"
}
```

## 17.5 L1-V 与四道门禁的关系

| 阶段 | 关键对象 | 通过条件 | 拒绝后的路径 |
| --- | --- | --- | --- |
| 门1 | 产品需求、预设计划、起点、预算 | 有效开工批准 | L2 整理；产品问题交 L1，计划问题交 L1-V |
| 门2 | 代码、构建来源、候选清单 | 冻结且来源可核验 | L2 分诊后调度 L3 或构建维护者 |
| V0 | 已授权 L1-V 候选包 | 候选、计划、策略、环境、预算匹配 | L2 分诊后提交 L1-V 或调度 L4；不启动验证 |
| 门3 | 完整证据、所有问题处置与重验 | 必需检查通过，证据有效，无未处置失败 | L4/验证器 → L2；按20.2分类处理 |
| 门4 | L5 申请、候选、证据、动作和目标 | 授权人有效批准，守卫再核验 | L5 → L2；按20.3分类处理，不直接派修 |

V0 仅检验已有授权是否适用，不能自行批准新阈值、新权限或新预算。计划与用例的语义变化必须重新批准 L1-V；如牵涉产品目标/AC，先完成 L1 决策。预授权只允许其明示范围内的候选重新绑定和设施恢复，不允许把失败检查改成可选。

## 17.6 推荐流程与回流路径

正向主线与回流入口见图1。下表中的完整路径均保留 L2：接收问题与最终处理不是同一个概念。L4 可以向 L3 询问复现信息，L3 可以提出根因假设，但双方不能建立绕过 L2 的正式修复派单。

| 问题根因 | 统一路径 | 处理结果 |
| --- | --- | --- |
| 需求或产品 AC | L4/L5 → L2 → L1 | 产品决策、新基线及受影响批准重新校验 |
| 任务拆分、方案或依赖 | L4/L5 → L2 | L2 更新计划与依赖，再按需派 L3；跨产品基线则升级 L1 |
| 代码实现缺陷 | L4/L5 → L2 → L3 | 修复提交、新候选、L1-V 重新绑定与 L4 重验 |
| 测试计划、用例、预期或验证器逻辑 | L4/L5 → L2 → L1-V | 独立维护者修订控制基线、重新授权并重验 |
| 环境、治具、服务或采集设施 | L4/L5 → L2 → L4 | 恢复设施，确认运行状态后按批准规则重验 |
| 交付目标、授权或执行状态 | L5 → L2 → L5／授权审批人 | 目标核对、更新申请、重新审批或核实实际执行结果 |
| 交付后产品缺陷 | L5 → L2 → 按根因分派 | 关联旧交付建立新问题/修复请求；实现问题派 L3，需求变化才升级 L1 |

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
