# 华为条款逐条覆盖总览

本表由 [coverage.json](coverage.json) 生成；维护方法和执行边界见 [使用说明](README.md)。

原文编号条款139项：原则21、规则68、建议50；另列治理补充6项，共145项。

全部已形成项目要求；ADOPTED表示按原意纳入，ADAPTED表示明确项目适配。二者均不表示产品已验证或PASS。

| 主题 | 原则 | 规则 | 建议 | 治理补充 |
| --- | ---: | ---: | ---: | ---: |
| [0 总体原则与规范治理](00.md) | 0 | 0 | 0 | 6 |
| [1 头文件](01.md) | 3 | 8 | 4 | 0 |
| [2 函数](02.md) | 2 | 7 | 6 | 0 |
| [3 命名](03.md) | 2 | 6 | 10 | 0 |
| [4 变量与数据结构](04.md) | 3 | 3 | 5 | 0 |
| [5 宏与常量](05.md) | 0 | 4 | 3 | 0 |
| [6 代码质量与内存](06.md) | 4 | 5 | 4 | 0 |
| [7 效率](07.md) | 2 | 0 | 4 | 0 |
| [8 注释](08.md) | 3 | 8 | 3 | 0 |
| [9 排版](09.md) | 0 | 6 | 2 | 0 |
| [10 表达式](10.md) | 0 | 1 | 4 | 0 |
| [11 编译与版本控制](11.md) | 0 | 4 | 1 | 0 |
| [12 可测性](12.md) | 1 | 4 | 1 | 0 |
| [13 安全](13.md) | 1 | 10 | 0 | 0 |
| [14 单元测试](14.md) | 0 | 1 | 1 | 0 |
| [15 可移植性](15.md) | 0 | 1 | 2 | 0 |

## 全部条目索引

| 条款 | 源页 | 项目要求主题 | 强度 | 采纳 | 项目规则 |
| --- | ---: | --- | --- | --- | --- |
| [HW-G-0.2-1](00.md#hw-g-0.2-1) | 5 | 可读性优先 | MUST | ADOPTED | GOV-001, READ-001 |
| [HW-G-0.2-2](00.md#hw-g-0.2-2) | 5 | 减少不必要复杂性 | MUST | ADOPTED | READ-001, READ-006 |
| [HW-G-0.2-3](00.md#hw-g-0.2-3) | 5 | 一致风格与迁移边界 | MUST | ADAPTED | C-001, GOV-001 |
| [HW-G-0.3-application](00.md#hw-g-0.3-application) | 6 | 适用范围与版本 | MUST | ADAPTED | GOV-001, C-001 |
| [HW-G-0.3-exceptions](00.md#hw-g-0.3-exceptions) | 6 | 例外必须经过授权评审 | MUST | ADAPTED | GOV-001, EVID-001 |
| [HW-G-0.4-levels](00.md#hw-g-0.4-levels) | 6 | 区分原则规则与建议 | MUST | ADOPTED | GOV-001, EVID-001 |
| [HW-P-1.1](01.md#hw-p-1.1) | 7 | 接口声明与实现分离 | MUST | ADAPTED | HEADER-001, ARCH-001 |
| [HW-P-1.2](01.md#hw-p-1.2) | 7 | 头文件职责聚焦 | MUST | ADOPTED | HEADER-001, ARCH-001 |
| [HW-P-1.3](01.md#hw-p-1.3) | 8 | 依赖指向稳定接口 | MUST | ADOPTED | HEADER-001, ARCH-001 |
| [HW-R-1.1](01.md#hw-r-1.1) | 8 | 源文件与公共接口配对 | MUST | ADAPTED | HEADER-001 |
| [HW-R-1.2](01.md#hw-r-1.2) | 9 | 包含依赖不得成环 | MUST | ADOPTED | HEADER-001 |
| [HW-R-1.3](01.md#hw-r-1.3) | 9 | 仅包含必要头文件 | MUST | ADOPTED | HEADER-001 |
| [HW-R-1.4](01.md#hw-r-1.4) | 9 | 头文件独立可用 | MUST | ADOPTED | HEADER-001 |
| [HW-R-1.5](01.md#hw-r-1.5) | 9 | 唯一且完整的包含保护 | MUST | ADOPTED | HEADER-001 |
| [HW-R-1.6](01.md#hw-r-1.6) | 10 | 变量定义留在实现文件 | MUST | ADAPTED | HEADER-001 |
| [HW-R-1.7](01.md#hw-r-1.7) | 10 | 通过正式头文件引用接口 | MUST | ADOPTED | HEADER-001, API-001 |
| [HW-R-1.8](01.md#hw-r-1.8) | 10 | 包含语句置于C链接块外 | MUST | ADOPTED | HEADER-001 |
| [HW-S-1.1](01.md#hw-s-1.1) | 11 | 模块目录与总接口 | SHOULD | ADOPTED | ARCH-001, HEADER-001 |
| [HW-S-1.2](01.md#hw-s-1.2) | 11 | 子模块提供独立接口 | SHOULD | ADOPTED | ARCH-001, HEADER-001 |
| [HW-S-1.3](01.md#hw-s-1.3) | 11 | 头文件扩展名一致 | SHOULD | ADOPTED | HEADER-001 |
| [HW-S-1.4](01.md#hw-s-1.4) | 12 | 统一包含排序 | SHOULD | ADAPTED | HEADER-001 |
| [HW-P-2.1](02.md#hw-p-2.1) | 12 | 一个函数一个职责 | MUST | ADOPTED | READ-001, API-001 |
| [HW-P-2.2](02.md#hw-p-2.2) | 13 | 消除有害重复 | MUST | ADAPTED | READ-006, READ-008 |
| [HW-R-2.1](02.md#hw-r-2.1) | 15 | 新增函数规模 | MUST | ADAPTED | C-001, READ-007 |
| [HW-R-2.2](02.md#hw-r-2.2) | 15 | 新增函数控制嵌套 | MUST | ADAPTED | C-001, READ-007 |
| [HW-R-2.3](02.md#hw-r-2.3) | 16 | 共享数据的可重入保护 | MUST | ADAPTED | RES-001, TIME-001 |
| [HW-R-2.4](02.md#hw-r-2.4) | 16 | 明确参数校验责任 | MUST | ADAPTED | API-001 |
| [HW-R-2.5](02.md#hw-r-2.5) | 17 | 完整处理失败反馈 | MUST | ADOPTED | ERR-001, API-001 |
| [HW-R-2.6](02.md#hw-r-2.6) | 18 | 合理调用依赖 | MUST | ADAPTED | C-001, READ-007 |
| [HW-R-2.7](02.md#hw-r-2.7) | 19 | 及时移除废弃代码 | MUST | ADAPTED | READ-006, C-001 |
| [HW-S-2.1](02.md#hw-s-2.1) | 19 | 只读参数使用const | SHOULD | ADOPTED | API-001 |
| [HW-S-2.2](02.md#hw-s-2.2) | 20 | 集中处理隐含状态与IO | SHOULD | ADAPTED | READ-004, RES-001 |
| [HW-S-2.3](02.md#hw-s-2.3) | 20 | 验证非参数输入 | SHOULD | ADOPTED | API-001, DATA-001 |
| [HW-S-2.4](02.md#hw-s-2.4) | 20 | 控制参数数量 | SHOULD | ADOPTED | API-001, READ-007 |
| [HW-S-2.5](02.md#hw-s-2.5) | 20 | 限制可变参数接口 | SHOULD | ADOPTED | API-001, C-001 |
| [HW-S-2.6](02.md#hw-s-2.6) | 21 | 文件内函数保持内部链接 | SHOULD | ADAPTED | C-001, HEADER-001 |
| [HW-P-3.1](03.md#hw-p-3.1) | 21 | 名称表达领域含义 | MUST | ADOPTED | READ-005 |
| [HW-P-3.2](03.md#hw-p-3.2) | 22 | 缩写可理解且统一 | MUST | ADOPTED | READ-005, C-001 |
| [HW-R-3.1](03.md#hw-r-3.1) | 22 | 项目命名风格一致 | MUST | ADAPTED | C-001, READ-005 |
| [HW-S-3.1](03.md#hw-s-3.1) | 22 | 相反动作成对命名 | SHOULD | ADOPTED | READ-005 |
| [HW-S-3.2](03.md#hw-s-3.2) | 23 | 避免无意义编号 | SHOULD | ADOPTED | READ-005 |
| [HW-S-3.3](03.md#hw-s-3.3) | 23 | 避免冗余组织前缀 | SHOULD | ADAPTED | READ-005, C-001 |
| [HW-S-3.4](03.md#hw-s-3.4) | 23 | 适配层保持平台习惯 | SHOULD | ADOPTED | C-001, ARCH-001 |
| [HW-S-3.5](03.md#hw-s-3.5) | 23 | 局部修改保持一致性 | SHOULD | ADOPTED | C-001, GOV-001 |
| [HW-S-3.6](03.md#hw-s-3.6) | 23 | 统一小写文件名 | SHOULD | ADOPTED | C-001, HEADER-001 |
| [HW-R-3.2](03.md#hw-r-3.2) | 23 | 外部全局变量前缀 | MUST | ADAPTED | C-001, RES-001 |
| [HW-R-3.3](03.md#hw-r-3.3) | 23 | 静态变量前缀 | MUST | ADAPTED | C-001, RES-001 |
| [HW-R-3.4](03.md#hw-r-3.4) | 24 | 变量名不能仅单字符 | MUST | ADOPTED | C-001, READ-005 |
| [HW-S-3.7](03.md#hw-s-3.7) | 24 | 不用类型编码代替语义 | MUST | ADAPTED | READ-005, C-001 |
| [HW-S-3.8](03.md#hw-s-3.8) | 24 | 变量名描述对象 | SHOULD | ADOPTED | READ-005 |
| [HW-S-3.9](03.md#hw-s-3.9) | 24 | 函数名描述动作 | SHOULD | ADOPTED | READ-005, API-001 |
| [HW-S-3.10](03.md#hw-s-3.10) | 24 | 函数指针名称表达行为 | SHOULD | ADOPTED | READ-005, API-001 |
| [HW-R-3.5](03.md#hw-r-3.5) | 24 | 常量名称统一 | MUST | ADAPTED | C-001 |
| [HW-R-3.6](03.md#hw-r-3.6) | 24 | 宏名避开保留模式 | MUST | ADAPTED | C-001, HEADER-001 |
| [HW-P-4.1](04.md#hw-p-4.1) | 25 | 变量职责单一 | MUST | ADOPTED | DATA-001, READ-005 |
| [HW-P-4.2](04.md#hw-p-4.2) | 25 | 结构体聚合相关数据 | MUST | ADOPTED | DATA-001, ARCH-001 |
| [HW-P-4.3](04.md#hw-p-4.3) | 26 | 限制全局可变状态 | MUST | ADAPTED | RES-001, ARCH-001 |
| [HW-R-4.1](04.md#hw-r-4.1) | 26 | 避免局部遮蔽全局 | MUST | ADOPTED | C-001, READ-005 |
| [HW-R-4.2](04.md#hw-r-4.2) | 26 | 通信数据显式编码 | MUST | ADAPTED | DATA-001, API-001 |
| [HW-R-4.3](04.md#hw-r-4.3) | 27 | 读取前必须初始化 | MUST | ADOPTED | DATA-001, ERR-001 |
| [HW-S-4.1](04.md#hw-s-4.1) | 27 | 全局状态单一写入责任 | SHOULD | ADOPTED | RES-001, ARCH-001 |
| [HW-S-4.2](04.md#hw-s-4.2) | 27 | 通过API访问模块状态 | SHOULD | ADOPTED | API-001, RES-001 |
| [HW-S-4.3](04.md#hw-s-4.3) | 27 | 靠近首次使用处初始化 | SHOULD | ADOPTED | DATA-001, READ-001 |
| [HW-S-4.4](04.md#hw-s-4.4) | 28 | 全局初始化有序 | SHOULD | ADOPTED | DATA-001, RES-001 |
| [HW-S-4.5](04.md#hw-s-4.5) | 28 | 减少无必要类型转换 | SHOULD | ADOPTED | DATA-001, C-001 |
| [HW-R-5.1](05.md#hw-r-5.1) | 28 | 表达式宏完整括号 | MUST | ADOPTED | C-001, READ-004 |
| [HW-R-5.2](05.md#hw-r-5.2) | 29 | 多语句宏形成单个语句 | MUST | ADOPTED | C-001 |
| [HW-R-5.3](05.md#hw-r-5.3) | 29 | 宏实参不得携带修改 | MUST | ADOPTED | C-001, READ-004 |
| [HW-R-5.4](05.md#hw-r-5.4) | 30 | 避免无含义字面值 | MUST | ADOPTED | C-001, READ-005 |
| [HW-S-5.1](05.md#hw-s-5.1) | 30 | 优先类型安全函数 | SHOULD | ADOPTED | C-001, READ-004 |
| [HW-S-5.2](05.md#hw-s-5.2) | 30 | 常量优先由语言表达 | SHOULD | ADAPTED | C-001, HEADER-001 |
| [HW-S-5.3](05.md#hw-s-5.3) | 31 | 宏不隐藏控制转移 | SHOULD | ADAPTED | READ-003, READ-004, C-001 |
| [HW-P-6.1](06.md#hw-p-6.1) | 32 | 质量属性有先后 | MUST | ADOPTED | GOV-001, ARCH-001 |
| [HW-P-6.2](06.md#hw-p-6.2) | 32 | 检查容易混淆的运算 | MUST | ADOPTED | READ-002, C-001 |
| [HW-P-6.3](06.md#hw-p-6.3) | 33 | 掌握目标内存布局 | MUST | ADOPTED | RES-001, TIME-001 |
| [HW-P-6.4](06.md#hw-p-6.4) | 33 | 核对实现隐含约束 | MUST | ADOPTED | API-001, RES-001 |
| [HW-R-6.1](06.md#hw-r-6.1) | 33 | 内存访问不越界 | MUST | ADOPTED | RES-001, TEST-002 |
| [HW-R-6.2](06.md#hw-r-6.2) | 34 | 所有资源有释放责任 | MUST | ADOPTED | RES-001, READ-003 |
| [HW-R-6.3](06.md#hw-r-6.3) | 34 | 不使用失效对象 | MUST | ADOPTED | RES-001, TEST-002 |
| [HW-R-6.4](06.md#hw-r-6.4) | 34 | 验证边界差一问题 | MUST | ADOPTED | TEST-002, RES-001 |
| [HW-R-6.5](06.md#hw-r-6.5) | 35 | 分支覆盖未知输入 | MUST | ADOPTED | C-001, ERR-001 |
| [HW-S-6.1](06.md#hw-s-6.1) | 35 | 函数资源收尾 | SHOULD | ADAPTED | RES-001, READ-003 |
| [HW-S-6.2](06.md#hw-s-6.2) | 35 | 评估单分支的反面情况 | SHOULD | ADAPTED | READ-002, ERR-001 |
| [HW-S-6.3](06.md#hw-s-6.3) | 35 | 项目禁止goto | MUST | ADAPTED | C-001, READ-003 |
| [HW-S-6.4](06.md#hw-s-6.4) | 36 | 评估运算上下溢 | SHOULD | ADOPTED | DATA-001, ERR-001 |
| [HW-P-7.1](07.md#hw-p-7.1) | 36 | 优化须有目标和证据 | MUST | ADOPTED | TIME-001, READ-008 |
| [HW-P-7.2](07.md#hw-p-7.2) | 37 | 优先算法和数据结构改进 | MUST | ADOPTED | ARCH-001, TIME-001 |
| [HW-S-7.1](07.md#hw-s-7.1) | 37 | 循环外提需保持语义 | SHOULD | ADAPTED | TIME-001, READ-008 |
| [HW-S-7.2](07.md#hw-s-7.2) | 38 | 数组访问考虑局部性 | SHOULD | ADOPTED | TIME-001, READ-008 |
| [HW-S-7.3](07.md#hw-s-7.3) | 38 | 评估资源池 | SHOULD | ADAPTED | RES-001, TIME-001 |
| [HW-S-7.4](07.md#hw-s-7.4) | 39 | 按实测选择内联 | SHOULD | ADAPTED | TIME-001, C-001 |
| [HW-P-8.1](08.md#hw-p-8.1) | 39 | 代码本身表达意图 | MUST | ADOPTED | READ-001, READ-005 |
| [HW-P-8.2](08.md#hw-p-8.2) | 39 | 注释准确无歧义 | MUST | ADOPTED | DOC-001, API-001 |
| [HW-P-8.3](08.md#hw-p-8.3) | 40 | 注释解释原因和约束 | MUST | ADOPTED | DOC-001 |
| [HW-R-8.1](08.md#hw-r-8.1) | 41 | 代码与注释同步更新 | MUST | ADOPTED | DOC-001, EVID-001 |
| [HW-R-8.2](08.md#hw-r-8.2) | 41 | 文件说明可追溯 | MUST | ADAPTED | DOC-001 |
| [HW-R-8.3](08.md#hw-r-8.3) | 41 | 声明契约与实现理由 | MUST | ADAPTED | DOC-001, API-001 |
| [HW-R-8.4](08.md#hw-r-8.4) | 41 | 全局状态有完整说明 | MUST | ADOPTED | DOC-001, RES-001 |
| [HW-R-8.5](08.md#hw-r-8.5) | 42 | 注释紧邻目标 | MUST | ADOPTED | DOC-001, C-001 |
| [HW-R-8.6](08.md#hw-r-8.6) | 42 | 明确case贯穿意图 | MUST | ADOPTED | DOC-001, C-001 |
| [HW-R-8.7](08.md#hw-r-8.7) | 42 | 注释缩写可理解 | MUST | ADOPTED | DOC-001, READ-005 |
| [HW-R-8.8](08.md#hw-r-8.8) | 42 | 统一注释风格 | MUST | ADAPTED | DOC-001 |
| [HW-S-8.1](08.md#hw-s-8.1) | 43 | 避免表达式中间插注释 | SHOULD | ADOPTED | DOC-001 |
| [HW-S-8.2](08.md#hw-s-8.2) | 43 | 注释语言适合维护者 | SHOULD | ADOPTED | DOC-001 |
| [HW-S-8.3](08.md#hw-s-8.3) | 43 | 工具可识别的文档格式 | MUST | ADAPTED | DOC-001 |
| [HW-R-9.1](09.md#hw-r-9.1) | 44 | 四空格缩进 | MUST | ADAPTED | C-001 |
| [HW-R-9.2](09.md#hw-r-9.2) | 44 | 逻辑段之间留空行 | MUST | ADOPTED | C-001 |
| [HW-R-9.3](09.md#hw-r-9.3) | 44 | 项目确定行宽和换行 | MUST | ADAPTED | C-001, READ-002 |
| [HW-R-9.4](09.md#hw-r-9.4) | 45 | 一行一个语句 | MUST | ADOPTED | C-001 |
| [HW-R-9.5](09.md#hw-r-9.5) | 45 | 控制语句清晰成块 | MUST | ADAPTED | C-001 |
| [HW-R-9.6](09.md#hw-r-9.6) | 45 | 运算符间距统一 | MUST | ADOPTED | C-001 |
| [HW-S-9.1](09.md#hw-s-9.1) | 46 | 注释分隔留空格 | SHOULD | ADOPTED | C-001, DOC-001 |
| [HW-S-9.2](09.md#hw-s-9.2) | 46 | 相关代码靠近 | SHOULD | ADOPTED | READ-001, READ-008 |
| [HW-R-10.1](10.md#hw-r-10.1) | 46 | 不依赖未规定求值顺序 | MUST | ADAPTED | READ-004, READ-008 |
| [HW-S-10.1](10.md#hw-s-10.1) | 47 | 复杂调用不嵌在实参中 | SHOULD | ADOPTED | READ-004, READ-008 |
| [HW-S-10.2](10.md#hw-s-10.2) | 48 | 赋值与条件及调用分开 | SHOULD | ADOPTED | READ-002, READ-008 |
| [HW-S-10.3](10.md#hw-s-10.3) | 48 | 括号表达分组意图 | SHOULD | ADAPTED | READ-002, DATA-001 |
| [HW-S-10.4](10.md#hw-s-10.4) | 49 | 布尔表达式不嵌赋值 | SHOULD | ADOPTED | READ-002, READ-008 |
| [HW-R-11.1](11.md#hw-r-11.1) | 49 | 严格告警并修复原因 | MUST | ADAPTED | TEST-003, GOV-001 |
| [HW-R-11.2](11.md#hw-r-11.2) | 49 | 统一编译和分析策略 | MUST | ADOPTED | TEST-003, GOV-001 |
| [HW-R-11.3](11.md#hw-r-11.3) | 49 | 本地与CI配置一致 | MUST | ADOPTED | TEST-003, EVID-001 |
| [HW-R-11.4](11.md#hw-r-11.4) | 50 | 受控版本及时集成 | MUST | ADOPTED | EVID-001, DEL-001 |
| [HW-S-11.1](11.md#hw-s-11.1) | 50 | 复制后重新审查语义 | SHOULD | ADOPTED | READ-006, TEST-002 |
| [HW-P-12.1](12.md#hw-p-12.1) | 50 | 模块具备清晰测试边界 | MUST | ADAPTED | ARCH-001, TEST-001 |
| [HW-R-12.1](12.md#hw-r-12.1) | 50 | 统一调测配置与源码 | MUST | ADAPTED | TEST-003, EVID-001 |
| [HW-R-12.2](12.md#hw-r-12.2) | 50 | 调测日志统一且可定位 | MUST | ADAPTED | LOG-001, TIME-001 |
| [HW-R-12.3](12.md#hw-r-12.3) | 50 | 断言表达内部不变量 | MUST | ADAPTED | TEST-001, ERR-001 |
| [HW-R-12.4](12.md#hw-r-12.4) | 50 | 外部错误用运行时处理 | MUST | ADOPTED | API-001, ERR-001 |
| [HW-S-12.1](12.md#hw-s-12.1) | 51 | 为测试准备故障通道 | SHOULD | ADAPTED | TEST-001, TEST-002 |
| [HW-P-13.1](13.md#hw-p-13.1) | 51 | 所有不可信输入先验证 | MUST | ADAPTED | API-001, TEST-002 |
| [HW-R-13.1](13.md#hw-r-13.1) | 51 | C字符串终止与容量正确 | MUST | ADAPTED | API-001, RES-001 |
| [HW-R-13.2](13.md#hw-r-13.2) | 52 | 不向固定数组无界写入 | MUST | ADOPTED | RES-001, TEST-002 |
| [HW-R-13.3](13.md#hw-r-13.3) | 52 | 防止整数运算溢出 | MUST | ADAPTED | DATA-001, RES-001 |
| [HW-R-13.4](13.md#hw-r-13.4) | 53 | 有无符号转换正确 | MUST | ADOPTED | DATA-001, API-001 |
| [HW-R-13.5](13.md#hw-r-13.5) | 55 | 窄化转换不丢失信息 | MUST | ADOPTED | DATA-001, API-001 |
| [HW-R-13.6](13.md#hw-r-13.6) | 56 | 格式与参数类型匹配 | MUST | ADOPTED | LOG-001, C-001 |
| [HW-R-13.7](13.md#hw-r-13.7) | 56 | 外部数据不充当格式串 | MUST | ADOPTED | LOG-001, API-001 |
| [HW-R-13.8](13.md#hw-r-13.8) | 57 | 二进制数据携带长度 | MUST | ADOPTED | DATA-001, API-001 |
| [HW-R-13.9](13.md#hw-r-13.9) | 58 | 字符IO先判断完整返回值 | MUST | ADOPTED | API-001, ERR-001 |
| [HW-R-13.10](13.md#hw-r-13.10) | 59 | 防止命令注入 | MUST | ADOPTED | API-001, TEST-002 |
| [HW-R-14.1](14.md#hw-r-14.1) | 59 | 测试随实现形成 | MUST | ADOPTED | TEST-001, TEST-002 |
| [HW-S-14.1](14.md#hw-s-14.1) | 60 | 测试关注行为 | SHOULD | ADAPTED | TEST-001 |
| [HW-R-15.1](15.md#hw-r-15.1) | 60 | 不得占用标准和平台保留名称 | MUST | ADOPTED | C-001 |
| [HW-S-15.1](15.md#hw-s-15.1) | 60 | 隔离平台依赖 | SHOULD | ADAPTED | ARCH-001, C-001 |
| [HW-S-15.2](15.md#hw-s-15.2) | 60 | 慎用内嵌汇编 | SHOULD | ADOPTED | ARCH-001, TIME-001 |
