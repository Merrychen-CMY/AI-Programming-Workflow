# ChatGPT 会员登录

ReqPilot 3.22.0新增官方 Sign in with ChatGPT（SIWC）接入，保留 L1、DOT 和独立执行/验证职责。普通 API Key 模式继续单独计费；两种模式不自动互相切换。

## 使用

1. 安装新版，或让兼容更新器加载新版；在尚未开工的会话调用 `workflow_setup`。
2. 选择“ChatGPT 会员登录”，点击 **Continue with ChatGPT**，在官方网页选择账户/工作区并允许使用订阅用量。不要将密码、授权码、Token发给L1。
3. 回到配置页点“刷新账户和模型”，选择账户实际可用的模型，填写Python/Codex路径，保存。
4. 点击“测试连接”。这会消耗所选来源的两次模型请求，只发送固定样例，检查工具调用和结构化输出。
5. 返回L1，检查环境、沟通需求、集中确认开工，然后按原流程自动推进并审核最终报告。

首次使用默认显示会员模式。已有API配置保持API模式，不能因升级静默改变计费来源。账户、模型和接入方式在开工时固定；本版不迁移已确认任务的登录方式。旧任务若需继续使用原配置，先在配置页选回原配置；需要会员模式时在新任务开工前选择。安装和配置不会重开最终报告、重置历史或自动重复旧操作。

会员接入仅用于原生Harness插件，CLI仍使用API Key；Codex执行会话的登录方式独立，不因DOT切换而改变。支持资格、模型和可用用量以官方实际返回为准，不把Plus/Pro等同于无限用量。

## 用量与恢复

- 会员请求不会读取普通API Key。出现429后保留账本，进入现有L1 API评估路径；不得为了继续执行改用付费API。
- 在[ChatGPT用量设置](https://chatgpt.com/settings/usage)管理应用用量及额外积分。应用获准使用额外积分与Platform API余额不是同一回事；如仅使用订阅内用量，请关闭官方设置中的超额积分权限。本插件不代用户更改该选项。
- 会员接口目前不支持 `max_output_tokens`。该配置值在会员模式不是服务端硬上限；保留调用次数、等待时间、总时间、截止时间和实际token记录。开工摘要与资源报告明确显示这一限制，不宣称实现货币硬限额。
- 已确认的授权/续期失效：在配置页对原账户重新登录；不能仅凭401/403认定登录过期。续期只处理OAuth凭据，不重放模型请求。模型调用429：查看官方用量/速率限制；L1提出等待或其他可行方案。响应未收到终止事件时如实保留未决结果，不能说请求未发送或自动重试。
- 本地退出会尝试撤销远端会话并清除本地Token，保留账户注册及主机ID。若远端撤销未确认，提示用户在官方设置断开应用。
- 多个会话/进程的续期由同一目录锁串行保护。`CHATGPT_BUSY`通常意味着续期中；宿主崩溃遗留锁时先核实没有运行中的登录/续期进程，再由允许的本地维护处理锁，不能重复使用旧刷新Token猜测成功。

## 登录失败如何处理（3.23.2）

浏览器地址变为 `127.0.0.1:<端口>/auth/callback`，说明已经返回本机。此时“后台兑换登录凭据 / HTTP 403”发生在Harness向OpenAI兑换授权码的请求，不是仍停在OpenAI登录页。浏览器能登录不证明Harness后台能访问相同服务。

诊断显示失败阶段、固定接口、HTTP状态、响应类型及允许公开的官方错误码；请求编号仅接受已知格式。不会输出响应正文、授权码、Token、任意服务端消息或带查询参数的回调地址。只复制页面正文，勿分享含授权码的地址栏。

| 诊断 | 处理方式 |
| --- | --- |
| `CHATGPT_MODELS_SIZE` | 模型目录超过8 MiB读取上限；保留登录，反馈该诊断排查目录大小，无需重新登录或充值。 |
| `CHATGPT_MODELS_EMPTY` | 请求成功但没有可供选择的模型；核对当前账户及应用权限，不猜填模型ID。 |
| `CHATGPT_ACCESS_HTML` / `CHATGPT_CHALLENGE` | 收到HTML拒绝页或明确的浏览器挑战；检查Harness后台的网络、代理和访问策略，不能据此断言账户或密钥失效。 |
| `CHATGPT_ACCESS` | 403尚无可识别的具体原因；根据阶段、响应类型与请求编号继续排查，不能直接认定是网络问题。 |
| `CHATGPT_CODE_EXPIRED` | 授权码无效；在配置页选原账户重新发起登录，不刷新旧回调页。 |
| `CHATGPT_REFRESH_INVALID` | 服务明确拒绝续期凭据；清除不可用Token，保留客户端注册与账户身份，重新登录原账户。 |
| `CHATGPT_CLIENT` / `CHATGPT_PERMISSION` | 核对客户端配置或授权范围；反复登录不能替代配置修复。 |
| `CHATGPT_INELIGIBLE` / `CHATGPT_REGION` | 核对官方账户资格、工作区策略或支持地区；不重复相同授权，不切换计费来源。 |
| `CHATGPT_RATE` / `CHATGPT_SERVICE` | 登录或模型目录限流/服务故障，保留凭据，稍后手动重试；旧授权码不得重放。 |
| `auth_discovery` / `auth_jwks` 阶段HTTP失败 | 身份配置或签名公钥获取失败，保留原HTTP分类；不再误报为JWT签名校验失败。 |

首次兑换失败仍保留OpenAI已签发的客户端ID，下次选原账户发起登录；临时403、HTML或网络故障不清除已有凭据。修复包只改诊断与明确失效凭据的恢复处理，不能保证外部403已消失。真实登录由用户发起；安装检查不会发送付费模型请求。

3.23.3为成功的模型目录响应设置独立8 MiB读取上限；OAuth和错误响应仍为500,000字节。模型目录附带的元数据不会全部送到页面，仅保留可展示模型的ID和名称及原顺序。旧版在此处出现HTTP 200加API_RESPONSE_SIZE时，应更新后重新打开配置页并刷新模型，无需重新登录。该修复不放宽任务材料或模型推理响应限制。

## 凭据与接入边界

OAuth采用回环回调、state、nonce、PKCE，并用固定版本jose验证签名、issuer、audience、过期时间和账户身份。凭据保存在Harness凭据服务，不写入任务、证据、源码或浏览器存储；保护强度由实际宿主服务决定。登录URL仅传给本机配置页，含ID提示的URL不得记录或发给模型。

会员模型请求通过本机控制器专用通道发给宿主；宿主取用凭据及续期，再调用公开的 `https://api.openai.com/v1/responses`。不访问ChatGPT内部backend-api，不复制Codex登录文件，不将Token交给Python或L3/L4工作进程。流式响应必须收到终止事件；响应原文和返回用量按现有API账本留存。

模型目录按账户获取；工具使用namespace，保留工具调用ID、历史与加密推理内容。API Key模式仍沿用原请求格式。插件不读取ChatGPT历史聊天，也不改变任务批准、工具权限和独立验证要求。

## 官方来源和验证范围

- [官方接入说明](https://developers.openai.com/siwc/token-sharing-open-source)
- [注册及登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [账户与续期](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [模型与调用](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [错误与恢复](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- [当前限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)

自测使用真实JWT签名验证、本地HTTP回调与真实Python管道，OAuth服务和模型响应为模拟；不据此声称你的账户已获准或已真实完成会员模型请求。首次真实授权必须由用户在官方页面完成，连接测试的额度消耗须由用户点击触发。
