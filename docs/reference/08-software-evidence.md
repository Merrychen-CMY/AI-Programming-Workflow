# 8 配置软件验证和可信证据

软件 wrapper 必须从已批准的 L1-V 验证任务包及其引用的产品 AC/验收计划获取检查清单，在干净环境检出 candidate_commit，校验仓库与 tree，运行开发测试和独立验收。验收脚本从固定 control_commit 读取；候选代码及编程 Agent 无权修改本次验证所用的验收标准。

每次检查均采集：实际命令参数、开始结束时间、退出码、发现和执行的用例数量、失败与跳过数量、stdout/stderr、结构化报告、环境指纹以及候选清单哈希。命令用参数数组传递，避免把不可信任务文本拼进 shell。

“退出码为 0”不充分：零测试、缺报告、必需用例被跳过、缺少 AC 映射都不得放行。软件套件可以按项目技术栈替换，CI 中运行的命令和验证器版本都应固定。

正式事件采用下面的自定义结构。为便于阅读展示为 JSON，写入 JSONL 时每条事件占一行。收集器先保存原始文件并计算哈希，再写事件；缺日志时不可先写 PASS。

```json
{
  "schema_version": 1,
  "event_id": "EVT-001",
  "run_id": "RUN-001",
  "attempt": 1,
  "candidate_manifest_sha256": "REPLACE_CANDIDATE_MANIFEST_SHA256",
  "policy_sha256": "REPLACE_POLICY_SHA256",
  "check_id": "hil.serial-ping",
  "ac_ids": ["AC-02"],
  "producer_id": "REPLACE_AUTHENTICATED_RUNNER_ID",
  "started_at": "2026-11-17T02:00:00Z",
  "finished_at": "2026-11-17T02:00:20Z",
  "outcome": "PASS",
  "exit_code": 0,
  "tests": {"expected": 1, "executed": 1, "failed": 0, "skipped": 0},
  "artifacts": [
    {
      "kind": "serial-log",
      "uri": "REPLACE_IMMUTABLE_LOG_URI",
      "sha256": "REPLACE_LOG_SHA256"
    }
  ],
  "attestation_ref": "REPLACE_TRUSTED_COLLECTOR_RECEIPT"
}
```

验证事件还须绑定 verification_request_id 与 verification_package_sha256；由收集器从可信运行上下文填入。每次回流重验使用新的 run_id，并关联 issue_id、resolution_task_id 和原失败 run_id，禁止只用 check_id 查找最近一次成功。

producer_id 字符串本身不证明身份。收集器必须从认证会话或受信 CI 运行中确定生产者，不能采用上传方任意填写的名字。使用签名时，验证器必须校验签名与可信公钥；使用受保护存储时，校验对象来源与访问权限。只加哈希链而允许同一代理重写整条链，不能建立可信性。

账本由单一收集器串行追加，按 event_id 去重，保留同一运行的所有尝试。封存 evidence-set.json 时列出候选哈希、必需检查、完整事件集合及所有原始文件哈希。门3绑定证据集合哈希，避免查询“最近一次 PASS”误命中别的候选。

dsh-evidence-ledger 保留为 DSH 工作台的记录与检索入口，可写入正式证据回执的指针。它不负责真实性判断，也没有自动把本教程字段变成可信证明的能力。[证据账本项目说明](https://github.com/Freakz2z/dsh-evidence-ledger)

不要把自定义事件字段直接当成插件的原生工具参数。适配器可用插件原生的 kind、status、claim、evidence、source、tags 保存摘要和回执地址；govrail 仍读取独立存储的权威事件。

```json
{
  "action": "record",
  "kind": "test",
  "status": "observed",
  "claim": "CAND-001 的正式验证已生成回执",
  "evidence": "REPLACE_RECEIPT_URI_AND_DIGEST",
  "source": "REPLACE_TRUSTED_RUN_URI",
  "tags": ["REQ-001", "CAND-001", "verification-receipt"]
}
```

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
