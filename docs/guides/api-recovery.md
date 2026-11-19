# API异常：先评估，再受控继续

3.7.0适用于本版完整记录的API异常。安装ABI5原生入口后，L1可使用`l1_review_api`，用户决定使用`workflow_review_api_retry`。不需要用户在聊天中提供密钥，不修改实际产品或真实任务记录来安装此能力。

## 正常处理顺序

1. DOT请求异常后保存错误分类、等待参数、实际耗时、请求身份（可取得时）及已知响应/用量。调用次数在发送前登记，无用量回执时仍为未知。
2. 工具正常返回`COMPLETED_API_REVIEW`，工作流显示`API_REVIEW_PENDING`；不会仅因本次API异常自动生成最终失败报告。产品原阶段及待根因分析问题保留。
3. L1读取`dot_status.api_review`和`resources`，核对本地响应、剩余工作、已用额度、错误原因和调整收益。先修复缺失凭据、网络或额度等实际问题，不能将所有错误当作等待不足。
4. 按[评估参数模板](../../templates/api-review.example.json)调用`l1_review_api`。`action=configure`调整后续等待；`action=retry`评估并申请下一次请求。两者均不调用模型、不派工、不重置预算。`reason/evidence/remaining_work/expected_benefit`必须具体。
5. 只有诊断明确为`NOT_SENT`、`REJECTED`或已收到终止响应`RESPONSE_RECEIVED`时，L1才能在当前授权内批准下一次请求。它仍是新的计数请求；收到失败/不完整响应不代表任务成功，也不代表免费。
6. 超时、连接重置、服务端5xx、无法解析/不完整的传输等结果不明时，进入`API_AUTHORIZATION_PENDING`。L1用`workflow_review_api_retry`展示准确评估、请求摘要、用量与可能重复计费风险。必须由真实用户选择允许下一次请求；自由文本、沉默或模型提供`approved`均无效。
7. 原生确认再次核对账本、请求/响应文件、产品指纹、当前额度、状态及截止时间。状态变化则拒绝，L1可重新评估生成新申请，旧申请保留在历史。拒绝后仍暂停，L1可调整方案或报告。
8. 条件满足后L1才显式调用`dot_plan`或`dot_run`继续。新的请求如再失败，重新评估；一次同意不能放行后续失败。需求/方案变化照常重新审查和验证，不借恢复复用失效PASS。

## 等待时间与资源

API网络等待参数默认120秒，L1可通过评估工具调整。实际传入传输层的值为：配置值、当前`limits.timeout_seconds`、剩余总时间和距截止时间的最小值。`timeout_seconds`作为单次操作上限，同时约束工作会话和API等待；开工摘要明确这项含义。需要更大当前额度时先`l1_adjust_limits`，资源追加由L1批准；改变用户明确截止时间仍需原生授权。不能通过调大等待时间免除API次数或延长总任务时限。

底层使用标准库HTTP网络超时；网络超时并不是服务端取消回执。无法保证服务端在本机结束等待时停止处理。当前没有API token/金额总预算或服务端取消工具，不能把未知用量按0计，也不能承诺不存在重复费用。

## 分类与下一责任人

| 诊断 | 已知范围 | 下一步 |
| --- | --- | --- |
| API_KEY_MISSING、API_DNS、API_CONNECT_REFUSED、API_TLS_CERTIFICATE | 明确未发送产品API请求 | L1核对配置/连接，评估后允许新请求 |
| API_AUTH、API_PERMISSION、API_RATE_OR_QUOTA、API_QUOTA，以及列明HTTP 400/404/413/422拒绝 | 收到明确拒绝 | L1先查权限、参数或额度，再评估；不得仅提高等待时间 |
| API_RESPONSE_INCOMPLETE，且有响应ID与failed/incomplete/cancelled终止状态 | 收到响应，已知部分用量按实保存 | L1评估输出额度或方案，不能执行未完成内容 |
| API_TIMEOUT、其他API_CONNECTION/API_TLS、5xx、传输/解析/大小异常 | 原请求可能已被处理，费用可能未知 | 保留UNKNOWN，只有原生用户批准后才允许新请求 |

诊断只返回受控错误码、HTTP状态、异常类型及安全格式的请求ID；不输出原始异常、代理响应正文或密钥。当前`store:false`，尚未实现服务端响应查询/取消；“核实”指核对本地账本与保存回执，不得声称已经查明服务器未处理。

## 历史与拒绝条件

`api-review.json`保存调整前后等待值、理由、L1评估、原生决定和摘要链。原`FAILED_OR_UNKNOWN`记录与缺失用量不改写成成功；新请求权限单独登记。状态与最终报告包含诊断、评估历史和真实累计用量。这是本地同账户审计记录，不是防恶意同账户进程的签名服务。

旧版未分类的中断、`RESERVED`请求、宿主取消/崩溃留下的`interrupted.json`、产品工作会话的`active_run`、已提交最终报告均拒绝此恢复入口。旧任务不能为了升级而删除标记或伪造诊断。旧未决API与终态仍按原任务升级规则拒绝迁移；安装新版不会自动恢复它们。

规划API失败、执行前DOT调度失败和GPT根因分析失败可以暂停评估。真正的实现/测试/审核进程中断仍走原UNKNOWN保护。无可行方案、用户拒绝且无法继续或达到不可增加的全任务上限时，L1提交真实最终报告；API结果仍未知时不能将其写成确定未执行或零费用。

3.8.0宿主状态采用简短回执：api_review.latest_attempt显示最新请求，unresolved保留当前未决列表，历史数量与完整记录分开。用dot_read_record读取api/ledger.json和api-review.json并按next_offset分页核验全部请求及授权；不要因为简短返回不含完整history就认为历史不存在。
