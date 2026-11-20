/**
 * @file led_pattern.c
 * @brief LED模块分工的主机教学示例，不是ESP32固件。
 * @author Codex（AI教学示例）
 * @date 2026-11-20
 * @version 1.0
 * @copyright 教学材料不声明产品版权或人员身份。
 * @details main组织流程，led_pattern计算颜色，led_driver模拟输出。
 * 调用方经各模块头文件使用接口，模块之间没有共享可变状态。
 */
#include "led_pattern.h"

/**
 * @brief 根据序号计算颜色标识。
 * @details 序号对周期取余后查询只读颜色表，没有全局可变状态或I/O。
 * @see led_pattern.h
 */
char led_pattern_get_color(unsigned step)
{
    static const char s_colors[LED_PATTERN_COLOR_COUNT] = {'R', 'G', 'B'}; /**< 固定颜色顺序。 */
    return s_colors[step % LED_PATTERN_COLOR_COUNT];      /* 循环选取当前颜色。 */
}
