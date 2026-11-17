# GitHub仓库与Agent使用

仓库内容应直接位于根目录，包含AGENTS.md、L1.md、DOT.md、templates/requirements.md；不要只上传ZIP或README。

给Agent固定提交下的 [启动提示](../../templates/agent-start.md)。初次只需说明“担任L1”，再描述原始需求。无需让用户先指定六层角色或填内部上下文表。

已有批准需求时，让DOT读入口和DOT.md，并提供真实批准文档及来源。私有仓库须有访问权限；让Agent记录实际读取的版本，不能假定一个链接就已经被完整读取。

发布规范前运行：

```powershell
python scripts/validate_repo.py
python -m unittest discover -s tests -v
```

GitHub Actions的“Workflow specification checks”只检查规范，不执行产品验收或部署。维护现有仓库时先保留用户改动，不覆盖未知工作。
