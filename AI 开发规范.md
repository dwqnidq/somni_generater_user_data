# AI Development Standards / AI 开发规范

> 适用于 somni_generater_user_data 项目中所有涉及 AI/LLM 的开发工作。

---

## 1. Prompt Engineering / 提示词工程

### 1.1 Prompt 文件管理

- 所有 prompt 模板统一存放于 `prompt/` 目录，按功能命名。
- 文件名格式：`generate_<数据类型>_<场景>.md`，例如 `generate_health_data__notice.md`。
- prompt 文件使用 Markdown 格式，结构清晰，包含：
  - **Role**：角色定义
  - **Context**：上下文信息
  - **Task**：具体任务描述
  - **Output Format**：输出格式要求（JSON Schema 或示例）
  - **Constraints**：约束条件

### 1.2 Prompt 编写原则

- **明确性**：避免模糊表述，指令具体可执行。
- **结构化**：使用 Markdown 标题、列表、代码块组织内容。
- **可复现**：同一 prompt + 同一输入应产出一致的结果（合理设置 temperature）。
- **中英文分离**：面向中文用户的输出用中文 prompt；面向开发调试的可保留英文。

### 1.3 Prompt 版本控制

- Prompt 修改必须提交 git，commit message 说明修改原因。
- 不要直接覆盖已有 prompt，重大变更时在 `docs/` 中记录变更日志。

---

## 2. LLM API 调用规范

### 2.1 API 调用

- API Key 通过 `.env` 文件注入，严禁硬编码在代码中。
- 使用 `python-dotenv` 加载环境变量。
- 统一封装 LLM 调用逻辑，避免在各脚本中重复编写请求代码。

### 2.2 错误处理

- 所有 API 调用必须包含超时设置（建议 30-60 秒）。
- 捕获并记录 API 错误（网络异常、限流、内容过滤等）。
- 实现指数退避重试机制（最多 3 次重试）。
- 失败时记录完整的请求上下文（去除敏感信息）以便排查。

### 2.3 Token 管理

- 监控单次请求的 token 消耗，避免超出模型上下文限制。
- 长文本分段处理时，确保上下文连贯性。
- 在批量生成脚本中记录总 token 使用量，便于成本核算。

### 2.4 Temperature 与参数选择

| 场景 | Temperature | 说明 |
|------|------------|------|
| 结构化数据生成（JSON） | 0.0 - 0.3 | 保证输出格式稳定 |
| 文案/描述生成 | 0.5 - 0.7 | 兼顾多样性与可控性 |
| 创意内容 | 0.7 - 1.0 | 需要更多随机性 |

---

## 3. 数据生成规范

### 3.1 生成流程

- 数据生成遵循 `user_gen_pipeline.py` 定义的流水线。
- 新增生成任务时，脚本放于 `scripts/generate_data/` 目录。
- 遵循"人格配置 → prompt 填充 → LLM 调用 → 结果校验 → 输出存储"的标准流程。

### 3.2 输出格式

- LLM 输出统一使用 JSON 格式，字段名使用 camelCase。
- 生成的 JSON 必须经过 schema 校验后再写入文件或数据库。
- 示例 JSON 存放于 `preview_llm_output/` 目录，用于开发阶段预览和调试。

### 3.3 数据校验

- 每个生成脚本应包含输出校验逻辑：
  - JSON 格式合法性
  - 必填字段存在性
  - 数值范围合理性（参考 `docs/personas/` 中的指标范围）
  - 人格编码一致性（参考 `docs/reference/睡眠人格编码说明.md`）

### 3.4 幂等性

- 重复执行同一生成任务不应产生重复数据。
- 使用唯一标识（如日期 + 人格 ID）作为输出文件名。

---

## 4. 代码规范

### 4.1 Python 代码风格

- 遵循 PEP 8 规范。
- 函数和类使用 docstring 说明用途。
- 类型注解（type hints）优先使用，提升代码可读性。
- 常量使用 UPPER_SNAKE_CASE，变量和函数使用 snake_case。

### 4.2 模块组织

- 工具函数放于 `utils.py`，按功能分组。
- 配置相关的常量放于 `config/` 目录。
- 避免循环导入，保持模块依赖关系清晰。

### 4.3 依赖管理

- 新增第三方库需同步更新 `requirements.txt`。
- 使用 `>=` 指定最低版本，避免锁定过时版本。
- 虚拟环境使用 `.venv/`，已在 `.gitignore` 中排除。

### 4.4 文件与路径

- 输出文件统一放于 `output/` 目录（已在 `.gitignore` 排除）。
- 敏感配置文件（`.env`、`config/config.json`）禁止提交到 git。
- 路径拼接使用 `os.path.join()` 或 `pathlib.Path`，保证跨平台兼容。

---

## 5. 文档规范

### 5.1 文档结构

| 目录 | 内容 |
|------|------|
| `docs/personas/` | 人格定义、睡眠指标、日程设定 |
| `docs/reference/` | 数据结构、编码说明、字段清单 |
| `docs/sleep_reference/` | 睡眠体征与环境补充说明 |
| `prompt/` | LLM 提示词模板 |

### 5.2 文档编写

- 使用中文编写面向业务的文档。
- 使用 Markdown 格式，善用表格、代码块、列表。
- 数据结构文档需包含字段名、类型、取值范围、示例值。
- 文档变更与代码变更一同提交。

---

## 6. 测试规范

### 6.1 LLM 输出测试

- 使用 `scripts/preview_llm_one_shot/` 下的脚本进行单条预览测试。
- 新增 prompt 或修改生成逻辑后，必须先运行预览脚本验证输出质量。
- 预览结果存入 `preview_llm_output/` 目录供 review。

### 6.2 单元测试

- 测试脚本放于 `test/` 目录。
- 测试重点：
  - JSON 解析与校验逻辑
  - 数据转换函数
  - 边界条件处理

### 6.3 集成测试

- 端到端测试需在 `.env` 中配置测试环境的 API Key。
- 测试数据与生产数据隔离，使用独立的数据库集合或文件前缀。

---

## 7. Git 工作流

### 7.1 Commit 规范

Commit message 格式：

```
<type>(<scope>): <subject>

<body>  # 可选，说明修改原因
```

常用 type：
- `feat`：新功能
- `fix`：修复
- `docs`：文档变更
- `refactor`：重构
- `prompt`：提示词变更

示例：
```
feat(sleep): 新增睡眠质量分析 LLM prompt
fix(pipeline): 修复人格配置加载时的空值异常
prompt(notice): 优化 notice 生成的输出格式要求
```

### 7.2 分支管理

- `main` 分支保持可用状态。
- 功能开发在特性分支进行，完成后合并回 `main`。
- 合并前确认 `preview_llm_output/` 中的样例输出正确。

### 7.3 敏感信息

- 提交前检查 diff，确保不包含 API Key、密码等敏感信息。
- `.env` 和 `config/config.json` 已在 `.gitignore` 中，但仍需人工确认。

---

## 8. 安全与隐私

### 8.1 API Key 管理

- API Key 仅存于 `.env`，通过环境变量读取。
- 不在日志、错误消息、生成数据中泄露 API Key。
- 不同环境（开发/测试/生产）使用不同的 Key。

### 8.2 用户数据

- 生成的用户数据为模拟数据，不包含真实用户信息。
- 如需使用真实数据进行测试，必须脱敏处理。
- `delete_user_data.py` 可用于清理已生成的数据。

### 8.3 输出内容审核

- LLM 生成的内容需经过合理性检查。
- 涉及健康、医疗建议的内容，确保措辞严谨，不包含误导性信息。
- 参考 `docs/personas/` 中的指标范围约束生成内容。

---

## 附录：快速检查清单

新增或修改 LLM 相关功能时，逐项确认：

- [ ] Prompt 文件放于 `prompt/` 目录，命名规范
- [ ] API Key 从 `.env` 读取，未硬编码
- [ ] 包含错误处理和重试逻辑
- [ ] 输出 JSON 经过 schema 校验
- [ ] 数值范围符合 `docs/personas/` 中的定义
- [ ] 预览脚本验证通过，样例存入 `preview_llm_output/`
- [ ] `requirements.txt` 已更新（如有新依赖）
- [ ] Commit message 遵循规范格式
- [ ] `.env` 和配置文件未提交到 git
