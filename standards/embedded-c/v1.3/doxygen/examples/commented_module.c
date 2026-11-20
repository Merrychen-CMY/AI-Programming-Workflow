/**
 * @file commented_module.c
 * @brief 有界采样累加器的合成教学实现，配合 commented_module.h 阅读。
 * @author Codex（AI 生成的教学示例，不代表任何产品作者或员工身份）
 * @date 2026-11-17
 * @version teaching-fixture-1.1
 * @copyright 本示例不声明产品版权归属；产品文件必须使用已核实的版权信息。
 * @details 头文件保存调用契约，定义上方说明完整使用条件与实现步骤。
 * 日期和版本只标识教学文件；不得作为生产元数据复制。
 * @par 作者及历史适用范围
 * 合成教学材料，无生产作者姓名或工号；实际产品按真实身份及已批准例外处理。
 * 教学修改记录：2026-11-17，Codex，建立示例；后续修改以 Git 历史追溯。
 * 2026-11-18，Codex，同步Allman、行后说明及函数定义文档。
 * @par 程序流程（逻辑概览）
 * 调用方先reset清空对象，再调用add追加采样；本模块不包含硬件或回调入口。
 * @verbatim
 * sample_accumulator_reset -> 清空total和count
 * sample_accumulator_add
 *   -> 检查输入、已有状态和剩余容量
 *   -> sample_batch_sum：检查整批值并计算总和
 *      失败 -> 返回错误，累加器和诊断计数不变
 *      成功 -> 更新total/count，再更新饱和的诊断计数
 * @endverbatim
 * @warning 编译和文档检查只能验证这个教学示例，不能替代产品测试或规范审查。
 */

#include "commented_module.h"

const uint32_t g_sample_api_version = 1U; /**< 只读教学接口版本，完整契约见头文件。 */

/**
 * @brief 进程启动以来成功追加的批次数，仅供教学调试观察。
 * @details 静态存储期，初值为零，在 UINT32_MAX 处饱和。不是产品统计 API，
 * 不对外暴露其地址。所有追加调用须串行化；不使用 volatile 冒充同步手段。
 * 长生命周期和并发说明按华为清晰性要求保留在对象上方。
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
    uint32_t sum = 0U;   /* 当前批次已检查采样值之和，只有全部有效才写入输出。 */
    size_t index;       /* 当前采样下标，访问范围受value_count限制。 */

    for (index = 0U; index < value_count; ++index)
    {
        if (values[index] > SAMPLE_MAX_VALUE) /* 任一采样越界时整批失败，不提交局部总和。 */
        {
            return SAMPLE_STATUS_VALUE_OUT_OF_RANGE;
        }

        sum += values[index]; /* 只累计已检查的值，暂不改调用方的数据。 */
    }

    *batch_total = sum; /* 整批都有效，再把总和交出去。 */
    return SAMPLE_STATUS_OK;
}

/**
 * @brief 初始化或清空调用者拥有的累加器。
 * @details 将total和count都设为零；允许未初始化的存储，不改变内部批次计数。
 * @par 参数与输出
 * accumulator [out]：可写且满足对齐要求的完整对象，允许为空；为空时不操作。
 * 对象归调用者所有，返回后可立即复用；没有返回值，不分配或保留资源。
 * @par 调用条件与步骤
 * 调用者保证对象无并发访问；本教学接口未验证ISR调用，不从ISR调用。
 * 先判空，再清空两个字段；不阻塞、无等待超时。声明处保留机器参数契约，
 * 此处以段落补充完整语义，避免Doxygen合并声明与定义时重复参数条目。
 */
void sample_accumulator_reset(sample_accumulator *accumulator)
{
    if (accumulator == NULL) /* 允许空对象，本次不执行清空。 */
    {
        return;
    }

    accumulator->total = 0U; /* 清空总和，让这个对象可以重新开始累计。 */
    accumulator->count = 0U;
}

/**
 * @brief 校验并一次性追加一批采样值。
 * @details 先检查指针、数量、累加器状态和容量，再检查整批值，最后提交。
 * @par 参数与输出
 * accumulator [in,out]：已由reset初始化的非空可读写对象，归调用者所有，
 * 不得与values区域重叠。values [in]：可读至少value_count个uint16_t的非空数组，
 * 各项无量纲，范围为0到SAMPLE_MAX_VALUE；value_count [in]单位为个，必须大于0，
 * 且不超过SAMPLE_MAX_COUNT减去当前count。输入缓冲区返回后可复用或释放。
 * @par 返回与副作用
 * SAMPLE_STATUS_OK表示总和与数量已增加；INVALID_ARGUMENT表示空指针、零数量
 * 或累加器字段越界；CAPACITY_EXCEEDED表示容量不足；VALUE_OUT_OF_RANGE表示采样越界。
 * 后三者为SAMPLE_STATUS_前缀的状态码，所有失败均保持累加器和诊断计数不变。
 * 成功批次计数达到UINT32_MAX后饱和；不得将这组多值错误码机械改成bool。
 * @par 并发、资源与边界
 * 所有实例的add调用须串行化；本教学接口未验证ISR调用，不从ISR调用。
 * 不保存指针，无堆分配、I/O、阻塞、异步受理或取消；最大检查SAMPLE_MAX_COUNT个元素。
 * 失效非空指针或区域重叠违反调用方前置条件，本实现不保证识别。
 * 声明处保留完整参数与返回契约，定义处段落描述同步维护。
 */
sample_status sample_accumulator_add(sample_accumulator *accumulator,
                                    const uint16_t *values,
                                    size_t value_count)
{
    uint32_t batch_total;   /* 整批校验成功后才能读取的批次总和。 */
    sample_status status;   /* 保留具体失败原因，返回给调用者。 */

    if ((accumulator == NULL) || (values == NULL) || (value_count == 0U)) /* 输入不完整。 */
    {
        return SAMPLE_STATUS_INVALID_ARGUMENT;
    }

    if ((accumulator->count > SAMPLE_MAX_COUNT) ||
        (accumulator->total > accumulator->count * SAMPLE_MAX_VALUE)) /* 已有状态无效。 */
    {
        return SAMPLE_STATUS_INVALID_ARGUMENT;
    }

    if (value_count > SAMPLE_MAX_COUNT - accumulator->count) /* 剩余容量装不下整批数据。 */
    {
        return SAMPLE_STATUS_CAPACITY_EXCEEDED;
    }

    /* 先检查整批数据；此处失败时，累加器还没有被改动（逻辑段总述）。 */
    status = sample_batch_sum(values, value_count, &batch_total);
    if (status != SAMPLE_STATUS_OK) /* 整批失败，保持累加器和诊断计数不变。 */
    {
        return status;
    }

    /* 检查全部通过，再一起更新总和与数量；调用者须保证无并发访问（逻辑段总述）。 */
    accumulator->total += batch_total;
    accumulator->count += value_count;
    if (s_successful_batches < UINT32_MAX) /* 诊断计数饱和后不回绕。 */
    {
        ++s_successful_batches;
    }

    return SAMPLE_STATUS_OK;
}
