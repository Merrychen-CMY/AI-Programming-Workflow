# 4 规划目录与事实来源

> 3.0适用范围：L1是唯一用户入口，在原始委托内代理批准需求、计划和变更，用户只审核L1最终报告。原技术契约保留，正式保护仍适用。见 [当前规则](../../AGENTS.md)；历史人工Ready/签名服务示例不自动授予L1身份或权限。

```text
product-repo/                    产品仓库
  src/                          产品实现，嵌入式项目可用 main/
  tests/                        开发自测
  AGENTS.md                     执行职责和范围
  gates.json                    本地 govrail 配置
  .github/workflows/            CI 入口
  .dsh/evidence-ledger.jsonl     工作记录副本，不作为权威凭据

control-repo/                    独立受保护的流程控制仓库
  policy/workflow.yaml          自定义流程策略
  policy/acceptance-plan.json   稳定 AC 到检查的映射
  adapters/                     任务、审批、证据、硬件接入
  acceptance/                   独立验收用例
  gates.json                    服务端正式门禁
  locks/                        工具版本和哈希清单

trusted-store/                  独立权限控制的存储
  approvals/                    审批服务生成的记录
  candidates/                   候选清单及其哈希
  runs/                         每次运行的原始日志和测试报告
  events.jsonl                  收集器生成的权威事件索引
  verdicts/                     门禁结论与最终交付回执
```

目录名仅为示例，不会自动形成权限隔离。正式验收读取 control-repo 的已批准提交，不能读取候选分支中被修改后的同名策略。产品仓库里的测试仍应运行，但独立验收条件由控制仓库提供。

审批与验证结果不回写进被测候选提交，避免“为了记录证据又改变候选版本”的循环。候选清单也放在代码提交之外；其哈希按保存后的原始字节计算，不把自己的哈希写进自己的计算内容。

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
