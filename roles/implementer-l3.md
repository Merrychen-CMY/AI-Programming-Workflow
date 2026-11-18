# L3 产品代码实现与修复

> 内部职责参考：唯一用户入口为 [L1](../L1.md)，[DOT](../DOT.md)是内部GPT调度者。DOT承担L2编排和L5汇总；L1-V为内部计划职责，不要求六个独立会话。正式工具契约见 [适用范围](../docs/advanced-profile.md)。

仅在用户或已授权的项目调度明确分配该角色时执行；阅读本文件不代表可以自动领取其他层职责。

## 职责

按 L2 任务实现和修复产品代码；保留自测、提交、变更说明与根因对应关系。

## 输入

L2正式任务、允许路径、代码起点、AC、自测要求；修复任务还需issue及分诊依据。

## 输出

代码提交、完整自测、变更说明、候选输入；不提交自称权威的验收PASS。

## 禁止事项

不接受 L4/L5 绕过 L2 的正式修复派单；不改正式验收标准、覆盖历史或自证验收通过。

缺输入、版本冲突或超授权时记录阻断并按L2统一入口提交；不能为了完成当前角色任务自行修订其他层的基线。交接使用 [交接模板](../templates/handoff.md)。


## 按需参考

- [7 配置 Codex 和 CAO 执行边界](../docs/reference/07-l3-implementation.md)
- [4 规划目录与事实来源](../docs/reference/04-directories.md)
- [8 配置软件验证和可信证据](../docs/reference/08-software-evidence.md)
- [20 统一回流调度与重新验证](../docs/reference/20-reflow.md)

[返回导航](../START_HERE.md)

嵌入式C任务按 [用户规范V1.3](../standards/embedded-c/README.md) 和 [职责映射](../policy/embedded-c.json) 补读本职责所需章节，维护规范规则到任务/验证证据的关联。
