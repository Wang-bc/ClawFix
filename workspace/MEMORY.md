# 稳定记忆

- Web 端仅支持文本类附件：`.txt` `.md` `.log` `.json` `.yaml` `.yml` `.csv`
- 所有案例和知识文档以 Markdown 文件为事实来源
- 历史案例补录后会写入 `workspace/cases/`
- 会话日志写入 `workspace/.sessions/`
- Delivery 队列写入 `workspace/.delivery/`
- 当配置了 `OPENAI_API_KEY` 且启用 `ENABLE_LLM=true` 时，系统会使用真实 LLM 进行分析与摘要
- 当配置了 `ENABLE_VECTOR_SEARCH=true` 时，系统会构建向量索引并执行混合检索

