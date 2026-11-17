/**
 * @file commented_module.h
 * @brief 有界采样累加器的合成教学接口，演示 Doxygen 注释与接口契约。
 * @author Codex（AI 生成的教学示例，不代表任何产品作者或员工身份）
 * @date 2026-11-17
 * @version teaching-fixture-1.0
 * @copyright 本示例不声明产品版权归属；产品文件必须使用已核实的版权信息。
 * @details 本文件与 commented_module.c 配套，仅使用 C99 标准类型。
 * 所有采样值均为无量纲整数，接口不访问设备、不分配内存、不执行异步操作。
 * 日期和版本只标识本教学文件；不得复制成实际产品的创建日期或版本。
 * @par 作者及历史适用范围
 * 本文件是明确标识的合成教学材料，没有生产作者姓名或工号。
 * 产品采用时应填写真实元数据；不适用字段按项目已批准的例外记录处理。
 * 教学修改记录：2026-11-17，Codex，建立注释示例；后续修改以 Git 历史追溯。
 * @warning 本示例生成文档或编译成功，不代表产品满足华为规范或通过验收。
 */

/**
 * @mainpage Doxygen 合成教学示例（可选首页）
 * @section fixture_scope 教学范围
 * 本页演示可选的首页语法，并非必需产品页面。产品可以删除整个 mainpage 块，
 * 或改用已纳入 INPUT 的 Markdown 首页；一个文档集只选一个首页来源。
 * @section fixture_usage 使用方式
 * 先调用 sample_accumulator_reset() 初始化，再调用 sample_accumulator_add()。
 * @see sample_accumulator_add
 */

#ifndef COMMENTED_MODULE_H
/** @brief 防止教学头文件在一个翻译单元内重复展开。 */
#define COMMENTED_MODULE_H

#include <stddef.h>
#include <stdint.h>

/** @brief 一个累加器最多接收的采样数量，单位：个。 */
#define SAMPLE_MAX_COUNT (8U)

/** @brief 每个采样值的上限，数值无量纲，下限为零。 */
#define SAMPLE_MAX_VALUE (1000U)

/** @brief 累加操作的完成状态，只有 SAMPLE_STATUS_OK 表示输出已更新。 */
typedef enum {
    SAMPLE_STATUS_OK = 0, /**< 操作完成，累加器已更新。 */
    SAMPLE_STATUS_INVALID_ARGUMENT, /**< 空指针、零数量或无效累加器状态。 */
    SAMPLE_STATUS_CAPACITY_EXCEEDED, /**< 新批次将超过累加器的数量上限。 */
    SAMPLE_STATUS_VALUE_OUT_OF_RANGE /**< 至少一个采样值超过 SAMPLE_MAX_VALUE。 */
} sample_status;

/**
 * @brief 由调用者拥有的有界累加结果。
 * @details 使用前调用 sample_accumulator_reset()。后续仅通过本模块接口修改。
 * 一份实例只能由一个执行上下文访问，或由调用者在所有访问之间提供互斥。
 */
typedef struct {
    uint32_t total; /**< 所有已接收采样值之和，无量纲，范围为 0 到 count * 1000。 */
    /**
     * @brief 已接收采样的数量，单位：个，范围为 0 到 SAMPLE_MAX_COUNT。
     * @details 此较长说明位于成员上方；短说明也可以位于同一行右侧。
     * 不把后置成员注释单独放到声明下方。
     */
    size_t count;
} sample_accumulator;

/**
 * @brief 教学接口的数值版本，值 1 表示本示例的第一版接口。
 * @details 程序静态存储期内有效，调用者只读，不转移任何资源所有权。
 * 真实产品应采用自身已批准的版本管理方式。
 */
extern const uint32_t g_sample_api_version;

/**
 * @brief 初始化或清空调用者拥有的累加器。
 * @details 将 total 和 count 同时设为零；未初始化的存储也可以传入。
 * 不分配资源、不阻塞、无超时，也不影响内部批次诊断计数。
 * @param[out] accumulator 指向可写且满足对齐要求的完整累加器对象；
 * 允许为空，为空时不执行操作。对象仍由调用者拥有，返回后可立即复用。
 * @note 调用者须保证本对象没有并发访问。本教学接口未验证中断调用，不从 ISR 调用。
 * @see sample_accumulator_add
 */
void sample_accumulator_reset(sample_accumulator *accumulator);

/**
 * @brief 校验并一次性追加一批采样值。
 * @details 先完成整个批次的范围检查，再提交 total 和 count。
 * 成功时 total 增加批次总和、count 增加 value_count；任何错误均保持
 * 累加器和内部成功批次计数不变。内部成功批次计数达到 UINT32_MAX 后保持饱和。
 * @param[in,out] accumulator 非空、可读写且已由 sample_accumulator_reset()
 * 初始化的完整对象；调用者拥有。对象不得与 values 指定的输入区域重叠。
 * @param[in] values 非空、可读取至少 value_count 个 uint16_t 的数组；
 * 每项无量纲，允许范围为 0 到 SAMPLE_MAX_VALUE。缓冲区归调用者所有。
 * @param[in] value_count 输入数组的有效元素数量，单位：个；必须大于零，
 * 且不超过 SAMPLE_MAX_COUNT 减去累加器当前 count 后的剩余容量。
 * @return 本次同步操作的完成状态。
 * @retval SAMPLE_STATUS_OK 整批校验完成，累加器已更新。
 * @retval SAMPLE_STATUS_INVALID_ARGUMENT 指针为空、数量为零，或累加器字段越界。
 * @retval SAMPLE_STATUS_CAPACITY_EXCEEDED 批次数量超过剩余容量。
 * @retval SAMPLE_STATUS_VALUE_OUT_OF_RANGE 存在超过 SAMPLE_MAX_VALUE 的采样值。
 * @note 不保存输入指针；返回后输入缓冲区可复用或释放。无堆分配、I/O、
 * 阻塞或等待超时；最大检查 SAMPLE_MAX_COUNT 个元素。无异步受理或取消阶段。
 * @warning 因共享内部诊断计数，所有实例的 add 调用都须由调用者串行化；
 * 本教学接口未验证中断调用，不从 ISR 调用。非空但失效的指针及区域重叠
 * 属于调用方违反前置条件，运行时检查不保证识别这些情况。
 * @see sample_accumulator_reset
 * @code{.c}
 * sample_accumulator accumulator;
 * const uint16_t values[] = {10U, 20U};
 * sample_status status;
 *
 * sample_accumulator_reset(&accumulator);
 * status = sample_accumulator_add(&accumulator, values, 2U);
 * if (status != SAMPLE_STATUS_OK) {
 *     // 调用者处理失败，不能把错误当成一次成功的追加。
 *     sample_accumulator_reset(&accumulator);
 * }
 * @endcode
 */
sample_status sample_accumulator_add(sample_accumulator *accumulator,
                                    const uint16_t *values,
                                    size_t value_count);

#endif
