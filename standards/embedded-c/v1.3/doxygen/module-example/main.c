/**
 * @file main.c
 * @brief LED模块分工的主机教学示例，不是ESP32固件。
 * @author Codex（AI教学示例）
 * @date 2026-11-20
 * @version 1.0
 * @copyright 教学材料不声明产品版权或人员身份。
 * @details main组织流程，led_pattern计算颜色，led_driver模拟输出。
 * 调用方经各模块头文件使用接口，模块之间没有共享可变状态。
 */
#include "led_pattern.h"
#include "led_driver.h"

/**
 * @brief 依次计算并输出三个颜色标识。
 * @return 全部写出为0，输出失败为1。
 * @details main -> led_pattern_get_color -> led_driver_send；错误立即返回。
 */
int main(void)
{
    for (unsigned step = 0; step < LED_PATTERN_COLOR_COUNT; ++step)
    {
        const char color = led_pattern_get_color(step);
        const int status = led_driver_send(color);       /* 把当前颜色交给模拟输出层。 */
        if (status != 0)
        {
            return 1;                                   /* 输出失败，停止后续步骤。 */
        }
    }
    return 0;
}
