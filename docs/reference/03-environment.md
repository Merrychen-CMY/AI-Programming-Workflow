# 3 准备环境与固定版本

> 3.0适用范围：L1是唯一用户入口，在原始委托内代理批准需求、计划和变更，用户只审核L1最终报告。原技术契约保留，正式保护仍适用。见 [当前规则](../../AGENTS.md)；历史人工Ready/签名服务示例不自动授予L1身份或权限。

准备两个仓库：产品代码仓库，以及受保护的流程控制仓库。后者保存策略、独立验收用例、wrapper 和适配器。另准备一个实现代理无写权限的证据存储。初期可以是另一系统账号管理的目录，正式使用可换为具备保留与访问控制的对象存储。

工具版本不要在运行时自动跟随 latest。先验证组合，再把实际版本与安装包哈希写入版本清单。DSH 插件还需要对应宿主版本兼容；dsh-spec-collab 项目文档列出了兼容范围和按 profile 安装方式。[dsh-spec-collab 安装说明](https://github.com/zx490336534/dsh-spec-collab)

| 组件 | 版本选择方法 | 配置完成的标志 |
| --- | --- | --- |
| DeepSeek Harness 与 spec 插件 | 按插件兼容范围选择并固定 | 能导出绑定版本的 Ready Spec |
| Dot 到执行器的桥接 | 记录连接器或自定义适配器版本 | 同一请求重复提交不会创建第二个任务 |
| Codex / CAO | 保留现有模型与账号配置，固定执行工具版本 | 任务有稳定 ID、状态和候选交付 |
| govrail | 本教程安装示例固定 0.52.0 | doctor 可运行，产品门禁能故意失败 |
| SiliconRig CLI 与 SDK | 选定官方发行版并记录哈希 | 能查询设备并运行指定测试固件 |
| ESP-IDF 与构建依赖 | 按项目选定并锁定 | 干净环境可重建固件 |

在产品仓库的初始化分支中配置 govrail，先审查其新增文件，再纳入版本管理。以下命令需要已安装 Python，并在产品仓库根目录执行：

```powershell
python -m venv .venv-gov
.\.venv-gov\Scripts\python.exe -m pip install "govrail==0.52.0"
.\.venv-gov\Scripts\gov.exe init --project . --hooks --ci
.\.venv-gov\Scripts\gov.exe doctor
.\.venv-gov\Scripts\gov.exe run
```

govrail 提供门禁运行与初始化机制，但默认检查不能代替本项目的验收和证据校验；需要后续注册自定义检查。[govrail 发行说明](https://pypi.org/project/govrail/0.52.0/)

SiliconRig CLI 从官方发行页选择适合操作系统的固定版本，保存版本和安装包哈希后加入 PATH。认证通过受控环境注入 SRIG_API_KEY，不写进任务提示词或仓库。先执行以下只读检查；板卡运行会消耗服务额度，应包含在门1预算内。[SiliconRig CLI](https://github.com/raws-labs/srig-cli)

```powershell
srig version
srig whoami
srig status
```

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
