# 7 配置 Codex 和 CAO 执行边界

> 3.0适用范围：L1是唯一用户入口，在原始委托内代理批准需求、计划和变更，用户只审核L1最终报告。原技术契约保留，正式保护仍适用。见 [当前规则](../../AGENTS.md)；历史人工Ready/签名服务示例不自动授予L1身份或权限。

产品仓库 AGENTS.md 应规定：L3 只执行 L2 下发且通过控制器校验的任务，从指定起点修改授权路径；不删除验收条件、不写权威证据、不直接合入主分支。L4/L5 的缺陷报告可以阅读和复现，但不能据此绕过 L2 建立正式修复任务。L3 的任务内自测迭代可按既有授权执行；对正式验收失败的修复须关联 L2 分诊单。

使用 CAO 时，先在安装位置运行其 wrapper 的 --paths 与 --help，按当前指南核对运行方式。保留原有模型、账号和 profile，不因接入流程而改写全局配置。将 CAO 的状态目录放在产品仓库之外，并持续复用同一个运行标识。

原有 CAO 接入约定包含 validate、dispatch、collect、verify 和 integrate，应以本项目固定版本的实际能力完成映射。任务内部验收与项目交付必须区分：CAO verify 可用于任务级独立检查；integrate 只允许进入 L2 指定的专用集成工作区。所有任务结果集成后，必须形成项目级候选，经 L1-V 与 V0 后进入 L4。

冻结候选分为两步：

1. 停止所有写入者，提交代码，记录候选提交、Git tree、子模块与依赖锁定信息。工作区必须干净；不能把未跟踪的必要文件留在提交之外。

2. 受控构建程序从此提交在干净环境构建，保存产物和环境记录；生成完整候选清单后通过门2。L4 后续测试该产物，不悄悄重建并替换它。

CAO 可能交付的是补丁或内部快照。适配器需先把它集成并转换成正式候选提交，再构建产物。不要把工具内部的 accepted 状态直接映射成门3通过。

```json
{
  "schema_version": 1,
  "candidate_id": "CAND-001",
  "launch_sha256": "REPLACE_LAUNCH_SHA256",
  "code_repository": "REPLACE_CODE_REPOSITORY",
  "base_commit": "REPLACE_FULL_BASE_COMMIT",
  "candidate_commit": "REPLACE_FULL_CANDIDATE_COMMIT",
  "candidate_tree": "REPLACE_GIT_TREE_ID",
  "control_commit": "REPLACE_APPROVED_CONTROL_COMMIT",
  "build_run_id": "BUILD-001",
  "build_environment_sha256": "REPLACE_ENVIRONMENT_MANIFEST_SHA256",
  "artifacts": [
    {
      "name": "firmware-flash-image.bin",
      "sha256": "REPLACE_BINARY_SHA256",
      "uri": "REPLACE_IMMUTABLE_ARTIFACT_URI"
    }
  ],
  "flash_layout_sha256": "REPLACE_FLASH_LAYOUT_SHA256"
}
```

ESP32 的产物清单需覆盖实际烧录集合，包括所需 bootloader、分区表、应用及烧录地址，或使用已验证适合目标板的合并镜像。不要默认任意 app.bin 都能单独完成烧录。

候选清单由受控构建或冻结程序生成，不仅相信 L3 的自报字段。代码、产物、依赖、工具链、重要构建配置或集成起点改变时，必须生成新 candidate_id；保留原候选及证据，不覆盖旧清单。L2 关联父候选和修复 issue_id；重新通过门2、L1-V 候选绑定、V0 及必需验证。具体重验规则见20.6。

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
