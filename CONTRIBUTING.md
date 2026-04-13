# Contributing to ClawFix

感谢你考虑为 ClawFix 做贡献。为了让协作成本可控、变更更容易审核，请在提交前遵循以下约定。

## 提交前准备

1. 先阅读 `README.md`，确认你的改动与项目目标一致。
2. 涉及较大功能、架构调整或接口变更时，建议先通过 Issue 或讨论说明方案。
3. 不要提交任何真实密钥、用户数据、会话记录、数据库或本地 IDE 配置。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.main
```

测试命令：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

## 代码与文档要求

- 尽量保持改动范围聚焦，避免一个 PR 同时混入无关重构。
- 新增功能时，同步补充必要的测试或说明原因。
- 变更接口、配置项、运行方式时，请同步更新 `README.md` 或相关文档。
- 尽量保持提交信息清晰，例如：`feat: add knowledge import validation`。

## Pull Request 检查清单

提交 PR 前，请确认：

- 改动目标清晰，标题能说明目的。
- 测试已执行，或者你已经说明无法执行的原因。
- 涉及 UI、API 或配置变更时，已附带截图、示例请求或配置说明。
- 没有提交 `.env`、`workspace/.sessions/`、`workspace/.delivery/`、本地数据库等敏感或无关文件。

## 讨论与支持

如果你不确定一个改动应放在代码、文档还是 `workspace` 中，请先开一个 Issue 或 Discussion，避免重复实现。
