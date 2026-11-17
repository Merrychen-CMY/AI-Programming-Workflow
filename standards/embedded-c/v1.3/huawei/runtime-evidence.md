# 逐条合规记录与运行时门禁

本门禁落实清单的执行记录，不是自动理解每个条款的C检查器。用户审核需求、执行计划和最终报告；DOT负责准备145个父项及当前清单全部50个子项的完整记录，用户不需要填写逐条表格。原文编号、条款强度、Doxygen和已认可的更严项目要求继续执行。

## 记录与身份

从[未评估模板](../../../../templates/huawei-compliance.example.json)开始。模板所有条款都是UNASSESSED/NOT_RUN，不能作为合规通过报告。记录顶层绑定规范版本、清单revision、完整清单内容的SHA-256、候选身份和实际实现会话。摘要使用[运行时模块](../../../../runtime/dot_runtime/compliance.py)的`catalog_digest`计算；清单内容、产品源码、配置或候选改变后不能沿用旧结论。

每个条目包含：`id`、`applicability`、`applicability_reason`、`result`、`verification_method`、`verifier_session_id`、`evidence`、`reason`和`exception_approval_id`。`evidence`是包含相对路径`path`和实际文件`sha256`的数组，路径以控制器指定的证据目录为根。不能用绝对路径、目录上跳或指向目录外的链接。

catalog中定义了`subrequirements`的父条款，记录必须在同名数组内包含精确且唯一的全部子ID。每个子项独立填写上述条目字段，继承顶层候选/规范基线；不继承父项的适用性、结果或例外批准。无子项的父条款允许省略数组或给出`[]`。子项不能放到顶层`clauses`冒充父项，不能移到另一父项下；父PASS或不适用都不能免除子项判断。MUST父项内的SHOULD子项按自身级别评估，合理偏离仍须独立依据。

| 情况 | 记录和门禁 |
| --- | --- |
| 尚未判断 | UNASSESSED或缺少条目阻断；145个父项及其全部50个子项必须完整且唯一 |
| 适用强制项 | APPLICABLE + PASS，有方法、独立验证身份和实际证据 |
| 适用建议 | 同样先评估；满足记PASS，合理偏离记DEVIATION并说明理由，仍需独立审查证据 |
| 不适用 | NOT_APPLICABLE + NOT_APPLICABLE，说明具体范围和理由，并保留独立适用性审查证据；不记PASS |
| 未运行或失败 | NOT_RUN、FAIL、BLOCKED、ERROR、SKIP均不能满足适用条款 |
| 强制项例外 | EXCEPTION，有原因和明确批准收据；不能以DEVIATION或PASS隐藏违反 |

文件头版本、日期、作者姓名及工号、版权、修改历史等必须真实；不能为满足字段伪造。省略或以版本控制代替必需元数据，以及无公开接口的私有.c无配对.h，均须正式例外，不能默认豁免。没有批准服务接线时，EXCEPTION保持阻断。

## 控制器接线

调用`validate_compliance(record, catalog, evidence_root, candidate_id, implementation_session_id, approved_exceptions=None, trusted_verifier_sessions=...)`，返回空数组才表示记录门禁通过。`catalog`来自控制器固定的规范基线。`trusted_verifier_sessions`只能来自已经真实完成、独立于实现的验证/审查会话登记，不能从报告自行收集。没有可信会话集合会直接阻断。

控制器可组织额外独立审查会话，让它基于候选源码、构建/测试记录和条款清单输出每个父项及子项判断；控制器补齐实际候选，并为审查输出中实际存在的父/子项绑定真实会话身份及原始输出文件摘要。控制器不能代填缺失子项、适用性或PASS。完整审查输出可被多条引用；条目需指出具体核对方法和事实依据，不能复制同一个“全部通过”空泛结论。

`verification_method`为明确非空文字，例如“审查本候选四空格缩进并引用格式检查日志”“检查Doxygen生成日志及公共API契约”，不是工具执行状态。只看代码无法证明的工具、并发、时序、物理或运行条件没有真实证据时，独立审查必须填NOT_RUN/BLOCKED；它不能通过写PASS创造测试事实。

可选`approved_exceptions`为控制器认证后提供的批准ID映射。收据包含`decision: APPROVED`、`approval_id`、`approved_by`、`clause_id`、`candidate_id`、`standard_version`、`coverage_revision`和`catalog_sha256`。必须绑定当前条款、候选和规范内容；实现者不能自行批准。记录里自填APPROVED、伪造批准人或嵌入另一份批准对象均不生效。调用方负责认证真实授权来源；不能把报告附带数据直接传作可信收据。

## 验证能力边界

门禁实际核验完整ID集合、字段和结果语义、候选与规范绑定、已登记独立会话、文件存在与摘要、路径范围及批准收据绑定。它不自动理解全部C代码，不证明证据文字真实，不代替工具执行、授权服务或人的最终验收。文件摘要仅证明当前文件与所引用内容一致。产品仍需配置真实格式工具、Doxygen、编译、静态分析及适用行为/实机检查；无法自动化的条款使用有事实依据的独立审查。

协议拒绝测试覆盖真实条款ID：缩进HW-R-9.1、Doxygen HW-S-8.3、源文件配对HW-R-1.1、可变参数建议HW-S-2.5和命令执行适用性HW-R-13.10等。测试中的合成证据明确标记为合成，不能冒充产品验收。

revision 3增加子项遗漏、重复、错归属、未评估、未执行、父PASS/不适用掩盖子项、虚假独立会话、缺失证据及伪造MUST例外等拒绝场景，也验证有理由的SHOULD偏离可保留。revision 2记录不能通过revision 3门禁；迁移需重新核对50个子项及受影响父项，不得复制旧PASS填满新模板。
