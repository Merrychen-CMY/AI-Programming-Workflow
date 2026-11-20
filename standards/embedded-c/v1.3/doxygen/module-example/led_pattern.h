/**
 * @file led_pattern.h
 * @brief LED模块分工的主机教学示例，不是ESP32固件。
 * @author Codex（AI教学示例）
 * @date 2026-11-20
 * @version 1.0
 * @copyright 教学材料不声明产品版权或人员身份。
 * @details main组织流程，led_pattern计算颜色，led_driver模拟输出。
 * 调用方经各模块头文件使用接口，模块之间没有共享可变状态。
 */
#ifndef REQPILOT_EXAMPLE_LED_PATTERN_H
#define REQPILOT_EXAMPLE_LED_PATTERN_H

/** @brief 示例按三个颜色形成一个周期。 */
#define LED_PATTERN_COLOR_COUNT 3U

/**
 * @brief 根据序号计算颜色标识。
 * @param[in] step 从零开始的事件序号。
 * @return R、G或B，不执行输出。
 */
char led_pattern_get_color(unsigned step);

#endif
