# somni_generater_user_data

用于生成与管理 Somni 侧用户相关数据（健康、睡眠事件、问卷、画像等）的脚本与文档仓库。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `config/` | 本地配置（如 `config.json`），敏感项勿提交；可用 `.env` 提供密钥。 |
| `docs/` | 业务与数据规范文档。 |
| `docs/personas/` | 人格与用户画像、睡眠指标范围、睡眠阶段说明等。 |
| `docs/reference/` | 数据结构、人格编码、字段清单等参考文档（原 `md/`）。 |
| `docs/sleep_reference/` | 睡眠体征与环境相关的补充说明（原 `sleepDocs/`）。 |
| `prompt/` | 大模型提示词模板。 |
| `scripts/` | 可执行脚本：`generate_data/`、`preview_llm_one_shot/`、`insert_data/` 等。 |
| `output/` | 生成产物目录（默认被 `.gitignore` 忽略）。 |
| `test/` | 测试脚本。 |

## 常用命令

在项目根目录执行（需已配置 `.env` 与 `config/config.json`）：

- 生成流水线：`python user_gen_pipeline.py`（按仓库内说明调整参数）。
- 单条 LLM 预览：`python scripts/preview_llm_one_shot/preview_sleep_main_summary.py --help`

更多子脚本见 `scripts/` 各子目录内文件头注释。

## 文档索引

- 人格与睡眠范围：`docs/personas/各人格睡眠指标范围.md`、`docs/personas/各人格睡眠阶段情况.md`
- 睡眠指标判定基准：`docs/personas/睡眠指标标准.md`
- 数据结构总览：`docs/reference/项目数据结构总览.md`
