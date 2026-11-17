# 上传 GitHub 与交给 Agent

## 上传内容

解压交付包，将 `ai-programming-workflow` **文件夹内的内容**作为仓库根目录上传。根目录应直接出现README.md、AGENTS.md、START_HERE.md和policy目录。不要只把ZIP文件上传，也不要只传README。保留.github、.gitattributes及.gitignore。

可以使用GitHub网页上传，或在本地用Git提交后推送。若已有同名仓库，先比较已有内容，不覆盖未知工作。GitHub操作细节见 [官方上传说明](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)。

## 发布前检查

```powershell
python scripts/validate_repo.py
python -m unittest discover -s tests -v
```

上传后若仓库启用了Actions，可查看名为“Workflow specification checks”的检查。它只验证规范仓库；不需要产品、HIL、审批或部署凭证。

## 给 Agent 的信息

复制 [agent-start.md](../../templates/agent-start.md)，填入仓库地址、**固定提交SHA**、产品仓库、本次任务和角色。远程浏览型Agent使用固定commit下的AGENTS.md和START_HERE.md链接；本地Agent使用已检出的目录。

例如入口地址格式为 `https://github.com/OWNER/REPO/blob/COMMIT/AGENTS.md`。OWNER、REPO、COMMIT均需替换。若Agent无法访问私有仓库，提供它有权限访问的本地检出目录或配置相应仓库访问；不能假定它已经看过链接。

要求Agent先回报读取到的版本、commit、角色与缺口，再开始已授权工作。只有仓库链接而没有任务与项目上下文时，它应停留在阅读和分析阶段。
