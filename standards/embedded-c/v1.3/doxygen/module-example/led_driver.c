/**
 * @file led_driver.c
 * @brief LED模块分工的主机教学示例，不是ESP32固件。
 * @author Codex（AI教学示例）
 * @date 2026-11-20
 * @version 1.0
 * @copyright 教学材料不声明产品版权或人员身份。
 * @details main组织流程，led_pattern计算颜色，led_driver模拟输出。
 * 调用方经各模块头文件使用接口，模块之间没有共享可变状态。
 */
#include "led_driver.h"
#include <stdio.h>

/**
 * @brief 将颜色写到示例输出端。
 * @details 同步调用putchar并转换为接口约定状态，失败直接交给调用方，不重试。
 * @see led_driver.h
 */
int led_driver_send(char color)
{
    const int result = putchar((unsigned char)color);
    if (result == EOF)                                   /* 输出失败，交给入口决定终止。 */
    {
        return 1;
    }
    return 0;
}
