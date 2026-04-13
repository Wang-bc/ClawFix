# GitHub 发布检查清单

本文面向第一次把项目推送到 GitHub 的维护者，按顺序执行即可。

## 一、发布前准备

1. 确认仓库是否打算公开。
如果仓库包含内部知识、客户案例、真实会话数据或公司密钥，优先使用私有仓库。

2. 检查敏感文件是否已忽略。
本项目建议不要提交以下内容：

- `.env`
- `.idea/`、`.jbeval/`
- `identifier.sqlite`
- `workspace/.sessions/`
- `workspace/.delivery/`
- `workspace/.index/`
- `workspace/.qdrant/`
- `workspace/cases/`
- `workspace/knowledge/uploads/`
- `workspace/memory/daily/`
- `docs/interview/`
- `智能助手功能列表.docx`

3. 确认公开仓库首页信息。
建议至少准备：

- 仓库名称
- 一句话描述
- README
- 开源许可证决定
- 联系方式或安全反馈方式

## 二、初始化本地 Git

如果当前目录还不是 Git 仓库，在项目根目录执行：

```powershell
git init
git branch -M main
```

然后检查准备提交的文件：

```powershell
git status
```

如果列表里出现 `.env`、`workspace/.sessions/`、`.idea/` 等文件，说明还需要继续调整 `.gitignore`。

## 三、第一次提交

确认无误后执行：

```powershell
git add .
git commit -m "chore: initialize repository"
```

## 四、在 GitHub 创建仓库

1. 登录 GitHub。
2. 点击右上角 `New repository`。
3. 填写仓库名，例如 `ClawFix`。
4. 选择 `Public` 或 `Private`。
5. 不要在 GitHub 页面勾选自动创建 README、`.gitignore` 或 License。
原因：这些文件已经在本地准备好了，避免产生额外合并提交。

## 五、关联远程仓库并推送

将下面命令中的地址替换成你的实际仓库地址：

```powershell
git remote add origin https://github.com/Wang-bc/ClawFix.git
git push -u origin main
```

如果你使用 SSH，也可以改成：

```powershell
git remote add origin git@github.com:<your-account>/<your-repo>.git
git push -u origin main
```

## 六、推送完成后建议补充的设置

1. 仓库基本信息

- 补充仓库描述
- 添加 Topics，例如 `fastapi`、`agent`、`llm`、`qdrant`
- 添加主页链接或演示地址（如果有）

2. 分支与协作设置

- 为 `main` 启用 Branch protection
- 要求 Pull Request 合并
- 需要时开启 Code Review

3. 安全与自动化

- 如需 CI，再添加 GitHub Actions
- 把密钥放到 GitHub Secrets，而不是写进代码库
- 开启 Dependabot 或安全扫描

## 七、许可证建议

如果你想让别人合法使用、修改和分发这个项目，需要补充 `LICENSE` 文件。常见选择：

- `MIT`: 最宽松，适合多数个人开源项目
- `Apache-2.0`: 更明确的专利授权条款
- `GPL-3.0`: 要求衍生作品继续开源

如果暂时不确定，就先不要公开仓库，或者先明确你的授权策略再发布。
