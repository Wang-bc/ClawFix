# Security Policy

在公开仓库前，请将本文中的占位联系方式替换为你的真实安全联络方式。

## Supported Versions

当前默认仅维护最新主分支。历史快照或长期未同步的分支不保证继续接收安全修复。

## Reporting a Vulnerability

如果你发现了安全问题，请不要先公开提交 Issue。建议按以下方式私下联系维护者：

- GitHub Private Vulnerability Reporting
- 邮箱：`security@example.com`（请替换为你的真实邮箱）

提交报告时，建议包含：

- 漏洞影响范围
- 复现步骤
- 可能的利用条件
- 建议修复方式或临时缓解方案

## Sensitive Data Checklist

以下内容不应提交到公开仓库：

- `.env` 与任何真实 API Key
- 飞书应用密钥
- 用户会话记录与诊断结果
- 本地数据库、索引文件、向量库文件
- 任何包含业务内部知识或隐私信息的上传文档
