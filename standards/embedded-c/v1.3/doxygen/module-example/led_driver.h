/**
 * @file led_driver.h
 * @brief LED模块分工的主机教学示例，不是ESP32固件。
 * @author Codex（AI教学示例）
 * @date 2026-11-20
 * @version 1.0
 * @copyright 教学材料不声明产品版权或人员身份。
 * @details main组织流程，led_pattern计算颜色，led_driver模拟输出。
 * 调用方经各模块头文件使用接口，模块之间没有共享可变状态。
 */
#ifndef REQPILOT_EXAMPLE_LED_DRIVER_H
#define REQPILOT_EXAMPLE_LED_DRIVER_H

/**
 * @brief 将颜色标识写到主机标准输出，模拟硬件输出边界。
 * @param[in] color 调用方提供的R、G或B标识。
 * @return 写出成功为0，标准输出失败为1。
 * @note 仅用于模块组织示例，不驱动真实LED。
 */
int led_driver_send(char color);

#endif
