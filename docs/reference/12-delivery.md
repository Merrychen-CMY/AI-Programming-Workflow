# 12 配置门4与交付回执

> 3.0适用范围：L1是唯一用户入口，在原始委托内代理批准需求、计划和变更，用户只审核L1最终报告。原技术契约保留，正式保护仍适用。见 [当前规则](../../AGENTS.md)；历史人工Ready/签名服务示例不自动授予L1身份或权限。

L5 的验收汇报至少包含：产品需求版本、L1-V 版本、候选版本、逐项 AC 结论、质量门禁、全部失败和重试历史、问题单处置及重验证据、未解决风险、拟执行动作、目标和回退方案。Dot 可承载汇报界面，但 L5 负责验收汇总与交付风险评估；其发现的问题必须提交 L2，不能绕过 L2 派修或自行更改 AC。

交付审批对象采用下面的自定义契约。审批服务对该对象哈希生成有效批准记录，不能把 approved: true 放进文件就视为通过。

```json
{
  "schema_version": 1,
  "candidate_manifest_sha256": "REPLACE_CANDIDATE_MANIFEST_SHA256",
  "evidence_set_sha256": "REPLACE_EVIDENCE_SET_SHA256",
  "verdict_sha256": "REPLACE_VERDICT_SHA256",
  "action": "merge",
  "target": "REPLACE_REPOSITORY_AND_MAIN_REF",
  "expected_target_commit": "REPLACE_TARGET_COMMIT",
  "expires_at": "REPLACE_APPROVAL_EXPIRY_UTC",
  "nonce": "REPLACE_SINGLE_USE_RANDOM_ID"
}
```

delivery_guard 执行前再次读取目标版本、批准撤销状态、证据有效期和产物哈希，并校验动作权限。目标不匹配立即停止，由 L5 提交 L2 分诊；L2 将目标或授权事项交回 L5 准备重新审批，涉及代码集成则协调 L3 生成新候选。不能自动替换目标、候选或批准内容。

先原子领取该批准的执行权，再执行动作，保存成功或失败回执。网络超时后应查询目标真实状态再恢复，防止重放合并或发布。动作完成后将批准标为已消费。合并批准不包含发布；发布批准应绑定具体固件哈希、环境或设备集合。

生产部署前制定回退版本、触发条件和负责人。硬件 OTA 回滚能力取决于分区设计和固件机制，应在需求与验收计划中另行确认；不能假设所有 ESP32 刷写都可自动回滚。

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
