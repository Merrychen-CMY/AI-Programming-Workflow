# 5 配置 L1 并生成开工包

> 3.0适用范围：L1是唯一用户入口，在原始委托内代理批准需求、计划和变更，用户只审核L1最终报告。原技术契约保留，正式保护仍适用。见 [当前规则](../../AGENTS.md)；历史人工Ready/签名服务示例不自动授予L1身份或权限。

默认3.0使用 [L1工具桥](../guides/l1-harness.md) 的独立代理批准记录。以下是保留的dsh-spec-collab人工Ready集成参考，不是默认自动审批的启动步骤。

如使用原插件人工Ready模式，在 DeepSeek Harness 中选择产品工作区，安装与当前宿主兼容的 dsh-spec-collab 固定版本。例如宿主已处于插件文档支持的版本范围时，可按其说明使用下面命令；desktop 与 web 必须对应实际启动的 profile。安装后重启相应进程或按界面要求启用。

```powershell
dsh plugin --profile desktop add dsh-spec-collab@0.4.1
```

默认L1写明目标、约束并创建delegated_l1基线；不调用或伪造插件人工Ready。以下正式适配示例中的产品需求 L1 写明目标、非目标、稳定验收编号 AC、约束、风险和未决事项，并形成受控 Product Ready Spec。Product L1 决定产品要实现什么以及产品验收目标。L4/L5 发现需求歧义或变化时先提交 L2，L2 整理影响和选项后提交 L1 决策；编程 Agent 不得自行修改。L1-V 负责独立验证需求和验收计划基线，详见第17节。

示例首个任务：ESP32-S3 启动后提供串口 PING 应答。AC-01 为复位后 5 秒内就绪；AC-02 为输入 PING 后 1 秒内返回 PONG；AC-03 为未知命令不崩溃且仍可继续响应 PING。这些业务阈值是教学示例，需在真实需求中确认。

需求导出适配器生成下面的开工包，自定义契约保存为 launch.json。所有提交使用完整 ID，仓库地址也参与绑定，不能只保存一个无来源的 SHA。

```json
{
  "schema_version": 1,
  "request_id": "REQ-001",
  "spec": {
    "repository": "REPLACE_SPEC_REPOSITORY",
    "path": "requirements/REQ-001/spec.md",
    "commit": "REPLACE_FULL_SPEC_COMMIT",
    "sha256": "REPLACE_SPEC_FILE_SHA256"
  },
  "code": {
    "repository": "REPLACE_CODE_REPOSITORY",
    "base_commit": "REPLACE_FULL_BASE_COMMIT"
  },
  "control_commit": "REPLACE_APPROVED_CONTROL_COMMIT",
  "acceptance_plan_sha256": "REPLACE_PLAN_SHA256",
  "allowed_paths": ["main/", "tests/"],
  "budget": {
    "max_attempts_per_task": 3,
    "max_parallel_tasks": 1,
    "max_hil_minutes": 30
  }
}
```

正式审批集成先保存开工包并计算 SHA-256。中间开工批准由受保护服务识别的L1代理身份在明确委托内完成；若该服务仅支持人工签名，则属于旧人工模式，不能声称符合默认全自动方案。审批服务记录审批者不可变账号 ID、动作 authorize_execution、包哈希、时间、撤销状态和回执。“approved_by: owner”这样的手写JSON不代表用户批准；本地delegated_l1记录也不冒充此正式服务的签名。

正式门1可接入已有身份验证和审计服务，并登记真实的代理权限；用户独立账号签署开工包属于可选旧人工模式。若用签名，校验公钥白名单保存在受保护控制环境，私钥不放在执行机器上。未完成这项配置前只能人工传递任务，不能宣称自动流程已具备不可冒认的审批。

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
