# 9 接入 SiliconRig 和 ESP32 HIL

> v2.0使用范围：本章保留原完整技术参考。默认用户流程为L1整理需求、用户批准、DOT执行；本章内部角色和正式工具契约按 [进阶范围](../advanced-profile.md) 适用，不要求用户逐层操作。

先做基础连通性检查。下面命令使用已冻结、确认烧录布局正确的镜像；输出文件存到本次 run 目录。READY 是示例固件需要实现的协议标记，不是 SiliconRig 自动生成的内容。

```powershell
srig run firmware-flash-image.bin --board esp32-s3 --expect "READY" --timeout 60s --log serial.log
```

srig run 的文档退出码为 0 成功、1 测试失败或超时、2 基础设施错误、130 中断。wrapper 应分别映射为 PASS、FAIL、ERROR、CANCELLED；只有完整验收通过才生成项目级 PASS。固件还可以输出工具识别的退出标记，因此 CLI 成功不可直接代替业务验收。[SiliconRig 命令行为](https://github.com/raws-labs/srig-cli)

正式 HIL 使用宿主机控制输入并核对实际输出。下面是 SDK 基本交互示意，展示如何对 AC-02 做外部断言；并非完整的三项验收套件。项目仍需补齐延迟测量、复位、异常输入、日志和设备身份采集。Board、expect、send、read_until 的基本用法见官方示例。[SiliconRig SDK 示例](https://github.com/raws-labs/srig-examples)

```python
from siliconrig import Board

def test_ping_response():
    with Board("esp32-s3", firmware="firmware-flash-image.bin") as board:
        board.expect("READY", timeout=5)
        board.send("PING\n")
        reply = board.read_until("PONG", timeout=1)
        assert "PONG" in reply
```

验收增强要求：每次交互清理旧缓冲并关联当前 session；用宿主机计时；必要时使用随机请求标识防止复用旧应答。板卡自报的固件版本只能作为辅助信息，仍要保存上传固件哈希与烧录回执。

| HIL 配置项 | 本教程默认规则 |
| --- | --- |
| 板卡匹配 | ESP32-S3；真实产品还要固定硬件版本与所需外设 |
| 会话独占 | 一次验证租用一块板卡，记录平台 session 和 device 身份 |
| 初始状态 | 按验收计划复位、清理残留并记录环境 |
| 固件一致性 | 烧录文件哈希必须等于候选清单 |
| 必需证据 | 会话信息、烧录结果、串口日志、测试报告、时间测量 |
| 基础设施重试 | 由 L2 的已授权恢复规则调度；初次运行外最多重试 2 次，保留全部结果 |
| 功能失败 | L4 提交 L2 分诊，不自动重跑到绿；L2 根据根因派发处理 |
| 结束清理 | 成功、失败、取消均释放本次会话；失联先查会话状态 |
| 缺失设备身份 | BLOCKED，不猜测、不用板卡型号替代设备身份 |

本表定义项目验收要求；平台字段能力需在接入时核对。缺少必需字段或治具不匹配时，L4 提交 L2 并保持 BLOCKED。L2 调度 L4 接入合适测试台，或提交 L1-V 修订验证方法并重新批准；若要降低产品 AC 或验收目标，必须进一步提交 Product L1 决策。不能伪造字段补齐。

需要 GPIO、传感器、网络干扰或多板交互时，应使用匹配治具。通用云板卡只能承担其实际可测的 AC；硬件不适用必须在验收计划中预先说明，不得由实现代理临时把必需 HIL 改成跳过。

---

[返回阅读导航](../../START_HERE.md) · [核心规则](../core-rules.md)
