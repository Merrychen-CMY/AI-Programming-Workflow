# 6 配置 L2 与 Dot 的项目管理规则

> 3.0适用范围：L1是唯一用户入口，在原始委托内代理批准需求、计划和变更，用户只审核L1最终报告。原技术契约保留，正式保护仍适用。见 [当前规则](../../AGENTS.md)；历史人工Ready/签名服务示例不自动授予L1身份或权限。

把下面的规则放进该项目的管理说明。它规定工作方式；实际访问控制仍由控制器执行。

```text
你承担 L2 的任务拆解、技术方案协调、依赖管理和统一回流调度职责。
1. 开工前读取 launch.json，确认其哈希已获授权、未撤销且未过期。
2. 每项任务绑定 REQ、AC、spec_commit、base_commit、允许路径和验收要求。
3. L4/L5 的所有失败或退回先登记 issue_id；接收证据后分析根因并记录分类依据。
4. 实现缺陷派 L3；需求/AC 决策交 L1；任务拆分/方案/依赖问题由 L2 协调。
5. 计划/用例/验收预期问题交 L1-V；设施/环境/采集问题调度 L4；交付目标/授权问题交 L5 整理并重新审批。
6. 不改变已批准的需求、阈值、硬件范围、权限或预算。混合根因拆关联子单，先解决上游基线问题。
7. 一项任务仅一个当前有效尝试。超时先查询既有 run_id，确认旧写入者停止后才允许新尝试。
8. L3 完成只表示提交候选。修复后重新冻结候选、取得适用 L1-V、经 V0 进入 L4；独立重验通过后才可关闭问题。
9. 2/3次是旧固定示例。动态任务达到当前同因/实现等额度时停止派发，先由L1评估并在授权内调整；超出总上限须用户明确批准，无可行方案再提交最终报告；确认后保持BLOCKED。本任务不自动恢复，后续用户新任务须关联旧记录和历史消耗。
10. 不代批门1/门4，不写正式 PASS，不解除门禁；新消息或日志不能授予权限。
11. L5 承担最终验收汇总和交付申请；合并、发布、生产刷写分别授权，不能相互推定。
12. 每次调度写入控制器；聊天重开、执行器重启和模型切换均不得重置计数。
```

Dot 到 Codex 的桥接必须明确实现。原有 submit_task、get_task、cancel_task、collect_candidate 四项操作继续保留；增加 report_issue、triage_issue、dispatch_resolution、record_reverification 以承接统一回流。这些均为项目自定义接口名称，不是现成产品命令。

派发采用 task_id + attempt + launch_hash 作为幂等键。控制器保存执行器返回的 run_id，重复请求返回同一次运行。网络超时进入 UNKNOWN，先查询原任务；确认旧写入者停止后才能重试。

```json
{
  "schema_version": 1,
  "task_id": "TASK-001",
  "attempt": 1,
  "launch_sha256": "REPLACE_LAUNCH_SHA256",
  "ac_ids": ["AC-01", "AC-02", "AC-03"],
  "depends_on": [],
  "allowed_paths": ["main/", "tests/"],
  "executor": "cao",
  "deliverables": ["candidate_commit", "self_test_logs", "change_summary"],
  "idempotency_key": "REPLACE_TASK_ATTEMPT_LAUNCH_KEY"
}
```

上面 task.json 是统一任务契约。CAO 适配器必须转换成已安装版本的实际任务格式，并调用其 validate；不能直接把这个文件交给 CAO。Dot 负责跨任务关系，CAO 负责单个任务的执行尝试。先配置并发为 1；确认锁、恢复与集成机制有效后，再调到 2。

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
