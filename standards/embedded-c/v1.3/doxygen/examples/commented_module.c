/**
 * @file commented_module.c
 * @brief 有界采样累加器的合成教学实现，配合 commented_module.h 阅读。
 * @author Codex（AI 生成的教学示例，不代表任何产品作者或员工身份）
 * @date 2026-11-17
 * @version teaching-fixture-1.0
 * @copyright 本示例不声明产品版权归属；产品文件必须使用已核实的版权信息。
 * @details 公共接口的完整契约统一保存在头文件，内部函数在本文件说明前置条件。
 * 日期和版本只标识教学文件；不得作为生产元数据复制。
 * @par 作者及历史适用范围
 * 合成教学材料，无生产作者姓名或工号；实际产品按真实身份及已批准例外处理。
 * 教学修改记录：2026-11-17，Codex，建立示例；后续修改以 Git 历史追溯。
 * @warning 编译和文档检查只能验证这个教学示例，不能替代产品测试或规范审查。
 */

#include "commented_module.h"

/* 此公开只读对象的契约见头文件中的同名声明。 */
const uint32_t g_sample_api_version = 1U;

/**
 * @brief 进程启动以来成功追加的批次数，仅供教学调试观察。
 * @details 静态存储期，初值为零，在 UINT32_MAX 处饱和。不是产品统计 API，
 * 不对外暴露其地址。所有追加调用须串行化；不使用 volatile 冒充同步手段。
 */
static uint32_t s_successful_batches = 0U;

/**
 * @brief 校验整批采样并计算总和，避免公共接口提交部分结果。
 * @details 调用者已保证指针有效、输入输出互不重叠，且 value_count 在
 * 1 到 SAMPLE_MAX_COUNT 之间；本函数负责采样值范围校验，不重复入口检查。
 * 总和上界为 8000，能够由 uint32_t 表示；若调整容量或值域，须重审这一约束。
 * @param[in] values 可读的 value_count 个采样值；每项无量纲，待检查是否不超过 1000。
 * @param[in] value_count 有效元素数量，单位：个，范围为 1 到 SAMPLE_MAX_COUNT。
 * @param[out] batch_total 非空、可写的 uint32_t 对象；成功时接收总和，失败时不变。
 * @return 校验完成状态。
 * @retval SAMPLE_STATUS_OK 所有值有效，batch_total 已写入。
 * @retval SAMPLE_STATUS_VALUE_OUT_OF_RANGE 至少一个值超出范围，batch_total 未写入。
 * @note 无全局副作用、不保存指针、不分配资源且不阻塞；所需时间受数量上限约束。
 * @see sample_accumulator_add
 */
static sample_status sample_batch_sum(const uint16_t *values,
                                     size_t value_count,
                                     uint32_t *batch_total)
{
    uint32_t sum = 0U;
    size_t index;

    for (index = 0U; index < value_count; ++index) {
        if (values[index] > SAMPLE_MAX_VALUE) {
            return SAMPLE_STATUS_VALUE_OUT_OF_RANGE;
        }

        sum += values[index];
    }

    *batch_total = sum;
    return SAMPLE_STATUS_OK;
}

/* 公共接口完整契约见头文件；实现中不维护第二份易失同步的副本。 */
void sample_accumulator_reset(sample_accumulator *accumulator)
{
    if (accumulator == NULL) {
        return;
    }

    accumulator->total = 0U;
    accumulator->count = 0U;
}

/* 先验证，再一次性提交；任何失败路径均不改变调用者对象。 */
sample_status sample_accumulator_add(sample_accumulator *accumulator,
                                    const uint16_t *values,
                                    size_t value_count)
{
    uint32_t batch_total;
    sample_status status;

    if ((accumulator == NULL) || (values == NULL) || (value_count == 0U)) {
        return SAMPLE_STATUS_INVALID_ARGUMENT;
    }

    if ((accumulator->count > SAMPLE_MAX_COUNT) ||
        (accumulator->total > accumulator->count * SAMPLE_MAX_VALUE)) {
        return SAMPLE_STATUS_INVALID_ARGUMENT;
    }

    if (value_count > SAMPLE_MAX_COUNT - accumulator->count) {
        return SAMPLE_STATUS_CAPACITY_EXCEEDED;
    }

    status = sample_batch_sum(values, value_count, &batch_total);
    if (status != SAMPLE_STATUS_OK) {
        return status;
    }

    accumulator->total += batch_total;
    accumulator->count += value_count;
    if (s_successful_batches < UINT32_MAX) {
        ++s_successful_batches;
    }

    return SAMPLE_STATUS_OK;
}
