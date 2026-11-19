# GitHub仓库与Agent使用

仓库内容应直接位于根目录，包含AGENTS.md、L1.md、DOT.md、templates/requirements.md；不要只上传ZIP或README。

给Agent固定提交下的 [启动提示](../../templates/agent-start.md)。初次只需说明“担任L1”，再描述原始需求。无需让用户先指定六层角色或填内部上下文表。

用户只向L1提供原始需求并集中确认开工边界。L1读入口和L1.md，先暂存、独立审查并批准需求，再调用GPT/DOT生成计划，初始化承接需求审核，随后独立审查并代理批准计划。计划未批准不能直接实现，成功或失败报告均经L1提交后由用户审核。私有仓库须有访问权限；让Agent记录实际读取的版本，不能假定一个链接就已经被完整读取。

发布规范前运行：

```powershell
python scripts/validate_repo.py
python -m unittest discover -s tests -v
```

GitHub Actions的“Workflow specification checks”检查规范和本地运行器测试，不执行真实产品验收或部署。维护现有仓库时先保留用户改动，不覆盖未知工作。
