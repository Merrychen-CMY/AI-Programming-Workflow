# 本地DOT运行器

入口为 `scripts/dot.py`，实现代码在 `runtime/dot_runtime/`。这是本地编排器，需要已安装可用的Codex和真实项目检查命令；文档存在不代表Codex登录、产品测试或硬件已接通。执行计划格式见 [JSON样例](../../templates/local-execution-plan.example.json)，命令以 `python scripts/dot.py --help` 为准。

## 准备与审核

L1生成需求，DOT准备执行计划的可读说明和运行配置，分别按 [需求模板](../../templates/requirements.md) 与 [执行计划模板](../../templates/execution-plan.md) 填写。`--plan`接收JSON，不直接接收Markdown：description写可读计划说明；allowed_paths写允许实现修改的仓库相对路径前缀；tasks写任务id、instruction与depends_on；verification写独立测试/审核指令、test_command参数数组与test_report_format；budgets写实现总尝试、同根因上限、设施额外重试及每次超时。计划实质内容必须完整进入该JSON或被明确绑定的基线，不可仅放在未被审批摘要覆盖的旁注。

必须把environment里的REPLACE_WITH占位改成真实工具链/目标/测试环境版本，并填写真实允许路径和测试命令；样例不能直接当作已配置、已批准计划。coding_standard选择huawei-c或有充分standard_scope_reason的none；检测到C源码时不能以none豁免华为规范。

`allowed_paths`是仓库相对路径前缀数组，例如按实际项目填写src、include或tests范围。运行器在实现修改后核对差异，超出批准范围就阻断；这是事后范围核验，不能阻止同账户进程的任意文件访问，也不能代替操作系统权限隔离。扩大路径范围须修改计划并重新审核。

`verification.test_command`是用户审核过的明确本地测试入口，使用参数数组（argv），不是一整段shell脚本。例如Python单元测试可填写 `["python", "-m", "unittest", "discover", "-s", "tests", "-v"]`，具体入口必须来自项目实际配置。控制器按 `shell=False` 独立运行该命令并保存退出码与原始输出，不依赖执行者打印一段测试摘要。独立测试Codex仍须按test_instructions检查候选、运行自己的独立检查并提交证据，不能用这条命令取代全部测试职责。

`test_report_format`当前只支持unittest、pytest或ctest。控制器从该真实命令输出识别非零实际执行测试数，并扣除可识别的跳过项；格式不支持、数量无法识别或命令失败均不能通过。Agent自填test_count或文字PASS不是这条门禁的证据，但解析摘要也不能证明测试充分性或排除被恶意伪造的测试程序，独立审核仍须核对检查入口、内容和AC覆盖。

批准的测试命令在候选的独立副本中运行，允许产生新构建日志；原候选输入文件被改动则阻断，原候选副本不用于生成临时文件。Codex测试/审核仍读取固定候选，临时测试或构建产物须放系统临时目录。CTest入口必须指向可在复制后重现的测试配置，不能沿用绑定到旧绝对路径的构建目录。工具链与系统环境由批准计划声明，独立验证需核实实际版本；当前本地执行器没有容器化工具链锁定能力。

```powershell
python scripts/dot.py init --project PATH_TO_PRODUCT --requirements PATH_TO_REQUIREMENTS --plan PATH_TO_PLAN --run-dir PATH_TO_PRIVATE_RUN
python scripts/dot.py status --run-dir PATH_TO_PRIVATE_RUN
python scripts/dot.py approve requirements --run-dir PATH_TO_PRIVATE_RUN
python scripts/dot.py approve plan --run-dir PATH_TO_PRIVATE_RUN
python scripts/dot.py run --run-dir PATH_TO_PRIVATE_RUN
python scripts/dot.py approve report --run-dir PATH_TO_PRIVATE_RUN
```

占位路径必须换为真实路径：产品使用Git仓库；需求与计划文件、运行目录均放在产品工作区之外，且需求/计划不能放运行目录内，防止普通执行者覆盖控制输入。所有内容保存在私有位置，不能提交真实批准或运行证据到公开规范仓库。`approve`在交互终端显示具体审核对象与摘要，由用户亲自核对并输入 `APPROVE <完整SHA256>` 确认；Agent不得替用户执行确认输入。只运行init不授权任何产品执行。

初始为REVIEW_PENDING；需求批准后PLAN_PENDING；计划批准后EXECUTING。成功或失败提交报告进入REPORT_PENDING；成功报告批准后DELIVERY_READY，失败/未完成报告批准后BLOCKED。没有批准或内容改变时拒绝依赖执行。预算和历史保留，重启、创建新会话或改计划不能清零后续成本。

## 修改需求或计划与恢复

审核不通过时，由用户在终端执行 `python scripts/dot.py reject report --reason "退回原因" --run-dir PATH_TO_PRIVATE_RUN` 并确认退回决定；requirements和plan也支持同样退回。退回进入BLOCKED，保留问题与累计次数，不把退回本身当作继续工作的授权。之后通过revise更新基线并重新审核，避免卡在无法修改的待审报告状态。

停止当前执行后，通过以下命令提交新基线。运行器保留历史与累计预算，撤销旧批准并返回REVIEW_PENDING；再由用户依次审核需求和计划。需追加预算时批准新的累计总上限，不能把已消耗次数当作新的一轮零起点。

```powershell
python scripts/dot.py revise --requirements PATH_TO_REQUIREMENTS --plan PATH_TO_PLAN --run-dir PATH_TO_PRIVATE_RUN
```

若旧运行结果UNKNOWN且仍有未确认进程，先审核未知/失败报告到BLOCKED，由用户实际检查旧进程已经停止、工作区及外部副作用已核对，再执行 `python scripts/dot.py reconcile --run-dir PATH_TO_PRIVATE_RUN` 并亲自确认摘要。该确认仅清理未决运行记录，不授予重试权限；随后仍须新基线及计划审核。不要删运行目录或开新目录绕过累计预算。

## 结果与能力边界

运行器维护审批记录、独立会话/候选/证据、尝试和最终报告；真实运行结果保存在所选运行目录。首次先用小项目核对实际工具与证据，再扩大范围。必需测试未跑、执行失败或缺证据均不能冒充成功。

本地运行要求可信开发机及已审核的测试入口：test_command只用于本地测试，不能夹带部署、刷写、网络发布或其他交付动作。命令及其调用的项目脚本使用本机账户权限；`shell=False`只避免把参数数组当shell字符串解析，不构成沙箱、服务器身份鉴权或对恶意同用户进程的隔离。

本地终端确认是本地信任模型：相信该操作系统账户由用户控制，运行目录未被其他同权限进程篡改。摘要检测内容漂移，不提供身份认证、数字签名或防篡改安全边界；多个进程/会话也不等于独立账户。需要更强保证时按 [正式接入](../advanced-profile.md) 配置受保护审批、工作区、CI与凭证边界。

Codex适配器使用每次独立的exec会话、JSON事件流和结构化结果，沿用已登录账户与默认模型；最低文件权限按实现或验证职责选择。参数以本机CLI帮助为准，接口依据 [官方非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。若本机Codex的沙箱工具无法启动，应先处理该环境故障，不能把输出格式正确或模型生成了报告当成已执行。

运行器没有外部交付执行器、受信审批服务或HIL设备服务。批准成功报告后停在DELIVERY_READY，不自动merge、release、部署或刷写，也不生成DELIVERED假回执。产品源码合规、真实产品测试与硬件验收必须由对应项目实际执行，仓库CI只验证本仓库行为和策略。
