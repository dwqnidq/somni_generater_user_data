# `scripts/generate_data` 目录说明

本目录存放**模拟/合成用户健康与睡眠相关数据**的 Python 脚本与共享配置模块。多数脚本从项目根目录的 `config/`、`output/`、`docs/` 读取或写入 JSON，部分脚本会连接 MongoDB 或调用外部 API（见各文件内注释与 `.env` 配置）。

**执行约定**：在仓库**项目根目录**下执行（脚本内通常会 `chdir` 到根目录并把根加入 `sys.path`）：

```bash
cd /path/to/somni_generater_user_data
python scripts/generate_data/<脚本相对路径>.py [参数]
```

凡使用 `argparse` 的脚本均可加 `-h` / `--help` 查看完整参数说明。

以下按**文件名**说明职责，并在「调用方式」列给出常用入口（与仓库当前文件列表一致）。

---

## 共享模块

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `persona_generation_config.py` | 从 `config/health_data_personas_config.json` 读取根级 `generation` 配置，与单人格的 `generation` 覆盖项做深度合并；提供 `DEFAULT_GENERATION` 默认值及 `load_personas_config`、`merge_generation` 供其他生成脚本复用。 | **无可执行入口**，仅作为模块被 `import`（例如 `from persona_generation_config import load_personas_config`）。 |

---

## 健康与睡眠核心数据

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `generate_health_data.py` | **体量最大的主生成管线**：按人格与业务规则生成用户健康/睡眠相关 JSON（含睡眠阶段、日程约束、体征与环境回填、睡眠事件、睡眠报告与可选 AI 分析等）。依赖 `personality_profile`、`utils` 等；可通过环境变量控制是否调用大模型。 | `python scripts/generate_data/generate_health_data.py`；常用：`--useModel True` / `False`、`--doubao`（等价开启模型）、`--workers N`、`--start` / `--end`（`YYYY-MM-DD`）、`--only-missing`、`--steps 逗号分隔阶段`、`--user UID`（可重复）、`--users id1,id2`。 |
| `generate_health_data_by_persona_config.py` | 仅依据 `health_data_personas_config.json` 生成各人格的睡眠健康数据（睡眠阶段、评分、单日结构等），不承载 `generate_health_data.py` 的全量能力，适合按配置快速产出结构化健康 JSON。 | `python scripts/generate_data/generate_health_data_by_persona_config.py`；可选：`--user-id`、`--state good|bad|mixed`、`--good-ratio`、`--start-date`、`--end-date`、`--overwrite`、`--config`。 |
| `generate_sleep_events_by_persona.py` | 根据人格配置与 `output` 下已有的 health / environment / vitals 数据，按规则生成**睡眠事件**（含阶段约束、异常与反馈等逻辑）。 | `python scripts/generate_data/generate_sleep_events_by_persona.py`；可选：`--user-id`、`--config`、`--overwrite`、`--start-date`、`--end-date`。 |
| `apply_event_impacts_by_persona.py` | 在已有 `sleep_events` 的前提下，将事件对睡眠的影响**回写**到 `environment_data` 与 `vitals_data`，保证环境与体征与事件一致。 | `python scripts/generate_data/apply_event_impacts_by_persona.py`；可选：`--user-id`、`--config`、`--start-date`、`--end-date`。 |
| `generate_somni_sleep_analysis_from_health.py` | 读取每用户 `{uid}_health_data.json` 与 `{uid}_sleep_events.json`，按产品规则生成 **`somni_sleep_analysis` 表结构**（按 uid × 记录日一条，含五维分数、14 日窗口统计等）。 | `python scripts/generate_data/generate_somni_sleep_analysis_from_health.py`；可选：`--output-dir`、`--out`（汇总 JSON）、`--uid`、`--window-days`、`--seed` 及各类 `--*-city-*` 同城参照参数。 |

---

## 环境与体征设备数据

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `generate_environment_data_by_persona.py` | 根据人格配置与 `{uid}_health_data.json` 生成**环境数据** JSON。 | `python scripts/generate_data/generate_environment_data_by_persona.py`；可选：`--user-id`、`--config`、`--overwrite`、`--start-date`、`--end-date`。 |
| `generate_vitals_data_by_persona.py` | 根据人格配置与 `{uid}_health_data.json` 生成**体征数据** JSON。 | `python scripts/generate_data/generate_vitals_data_by_persona.py`；可选：`--user-id`、`--config`、`--overwrite`、`--start-date`、`--end-date`。 |
| `generate_wearable_vitals_data.py` | 生成**可穿戴设备**形态的体征数据，并将合理范围写回 config。 | `python scripts/generate_data/generate_wearable_vitals_data.py`；可选：`--config`、`--portraits`、`--output-dir`、`--records-per-day`、`--seed`。 |
| `generate_radar_vitals_data.py` | 生成与 **radar** 数据源 `vitals_data.json` **结构一致**的体征数据。 | `python scripts/generate_data/generate_radar_vitals_data.py --uid <必填>`；可选：`--start-date`、`--end-date`、`--records-per-day`、`--seed`、`--data-source`、`--device-id`、`--session-id`、`--output`（支持 `{uid}` 占位符，默认 `output/{uid}_vitals_data.json`）。 |
| `generate_oximeter_data.py` | 生成**血氧仪**数据（`data_source=oximeter`，主要为血氧相关字段）。 | `python scripts/generate_data/generate_oximeter_data.py`；可选：`--config`、`--output-dir`、`--records-per-day`、`--seed`。 |
| `generate_sphygmomanometer_data.py` | 生成**血压计**数据（`data_source=sphygmomanometer`，收缩压/舒张压）。 | `python scripts/generate_data/generate_sphygmomanometer_data.py`；可选：`--config`、`--output-dir`、`--records-per-day`、`--seed`。 |

---

## 日历、天气、路况与艺术化展示

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `generate_calendar_events.py` | 根据 `docs/personas/各人格日程设定.md` 内嵌的日程模板，为各用户生成**日历事件**（默认 2026-03 整月），输出 `output/{user_id}_calendar_events.json`。 | `python scripts/generate_data/generate_calendar_events.py`（**无 CLI 参数**，日期与人物日程在脚本常量 `SCHEDULES` 中维护）。 |
| `generate_weather_traffic_data.py` | 按人格生成**天气**（深圳单日快照）与**通勤路况**（北京路线；晨型/夜型差异），写入 `output/{uid}_weather_data.json`、`_traffic_data.json`。 | `python scripts/generate_data/generate_weather_traffic_data.py`（**无 CLI 参数**，人格列表与快照日期在脚本内常量配置）。 |
| `generate_user_weather_snapshot_json.py` | 为每用户生成一份**对齐指定 schema** 的天气结构 JSON（如 `uv_index`、`humidity`、`temperature` 等），默认 `output/{user_id}_weather.json`。 | `python scripts/generate_data/generate_user_weather_snapshot_json.py`；可选：`--config`、`--output-dir`、`--overwrite`。 |
| `generate_beijing_link_traffic_data.py` | 为每用户生成一条**北京路段 Link 实时状态**样例（含 `linkId`、`speed`、`geometry` 等），输出 `output/{uid}_traffic_link_realtime.json`。 | `python scripts/generate_data/generate_beijing_link_traffic_data.py`；可选：`--user-id`、`--timestamp`（Unix 秒）、`--overwrite`。 |
| `generate_sleep_art_data.py` | 基于 `_health_data.json` 提取深睡、效率等指标，生成**睡眠艺术/星球可视化**侧使用的每日数据，星球占位字段可为默认空/零，输出 `output/{uid}_sleep_art_data.json`。 | `python scripts/generate_data/generate_sleep_art_data.py`（**无 CLI 参数**，用户列表在脚本内配置）。 |

---

## 情绪与步数

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `generate_daily_emotion_steps_data.py` | 根据 `health_data_personas_config.json` 生成**每日情绪分与步数**记录。 | `python scripts/generate_data/generate_daily_emotion_steps_data.py`；可选：`--user-id`、`--state`、`--good-ratio`、`--max-bad-days-per-week`、`--start-date`、`--end-date`、`--overwrite`、`--config`、`--seed`。 |

---

## LLM 批量生成（`generate_ai/`）

以下脚本从 `output/` 读取用户健康/睡眠等 JSON，调用大模型后写入汇总文件（路径、重试等见各脚本 `-h`）。默认均在项目根执行：

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `generate_ai/generate_sleep_pattern_commonality.py` | 读取各用户健康、环境、日历、情绪步数等，以 `record_date` 为锚做 **14 天窗口**，调用大模型批量生成 **`sleep_pattern_commonality`**（**JSON 数组**，每项 `highlight` / `analysis`），写入 `--out` 指定汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_sleep_pattern_commonality.py`；常用：`--output-dir`、`--out`、`--uid`、`--start-date`、`--end-date`、`--retry-delay`、`--system-prompt`。 |
| `generate_ai/generate_sleep_pattern_commonality_insight.py` | 基于共性分析结果等上下文，生成 **insight 摘要**类文案，写入汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_sleep_pattern_commonality_insight.py`；常用：`--output-dir`、`--out`（默认 `output/sleep_pattern_commonality_insight.json`）、`--uid`、`--start-date`、`--end-date`、`--system-prompt`、`--retry-delay`。 |
| `generate_ai/generate_ai_analysis_14d.py` | 生成 **14 日 AI 分析**汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_ai_analysis_14d.py`；常用：`--output-dir`、`--out`（默认 `output/ai_analysis_14d.json`）、`--uid`、`--start-date`、`--end-date`、`--retry-delay`。 |
| `generate_ai/generate_sleep_main_summary.py` | 生成 **睡眠主摘要**汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_sleep_main_summary.py`；常用：`--output-dir`、`--out`（默认 `output/sleep_main_summary.json`）、`--uid`、`--start-date`、`--end-date`、`--retry-delay`。 |
| `generate_ai/generate_sleep_notice.py` | 生成 **睡眠 notice** 汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_sleep_notice.py`；常用：`--output-dir`、`--out`（默认 `output/sleep_notice.json`）、`--uid`、`--start-date`、`--end-date`、`--personality-type`、`--retry-delay`。 |
| `generate_ai/generate_sleep_quality.py` | 生成 **睡眠质量分析**汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_sleep_quality.py`；常用：`--output-dir`、`--out`（默认 `output/sleep_quality.json`）、`--uid`、`--start-date`、`--end-date`、`--retry-delay`。 |
| `generate_ai/generate_sleep_ai_intervention.py` | 生成 **睡眠 AI 干预**建议汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_sleep_ai_intervention.py`；常用：`--output-dir`、`--out`（默认 `output/sleep_ai_intervention.json`）、`--uid`、`--start-date`、`--end-date`、`--retry-delay`。 |
| `generate_ai/generate_sleep_event_environment_intervention.py` | 生成 **睡眠事件 × 环境干预**分析汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_sleep_event_environment_intervention.py`；常用：`--output-dir`、`--out`、`--uid`、`--start-date`、`--end-date`、`--retry-delay`。 |
| `generate_ai/generate_morning_timeline_alarm_context_advisory.py` | 生成 **晨间时间线 / 闹钟情境建议**汇总 JSON。 | `python scripts/generate_data/generate_ai/generate_morning_timeline_alarm_context_advisory.py`；常用：`--output-dir`、`--out`（默认 `output/morning_alarm_insight.json`）、`--uid`、`--start-date`、`--end-date`、`--system-prompt`、`--retry-delay`。 |
| `generate_ai/__init__.py` | 包初始化文件。 | 无需直接执行。 |

---

## 睡眠地图与热力图模拟

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `generate_sleep_map_ranking_data.py` | 生成**北京市各区睡眠地图排行**用的 `somni_sleep_analysis` 形态数据（含五维权重与公式、区级批量与同城均值等）。 | `python scripts/generate_data/generate_sleep_map_ranking_data.py`；可选：`--count`、`--date`（`stats_date`）、`--seed`、`--output`。 |
| `generate_sleep_map_pool.py` | **推荐**：按**指定日期范围**一键生成 `somni_sleep_analysis.json` + `somni_sleep_district.json`（内部依次调用 multi_user → aggregated）。 | `python scripts/generate_data/generate_sleep_map_pool.py --start-date 2026-04-01 --end-date 2026-05-31`；可选：`--users-per-district`、`--seed`、`--aggregated-only`（仅重聚合）。 |
| `generate_sleep_map_multi_user.py` | 生成**多用户、多日期**睡眠地图数据（随机 ObjectId、按区分层用户质量、日期范围可配），用于 `beijing_sleep_map_multi_user.json` 一类场景。 | `python scripts/generate_data/generate_sleep_map_multi_user.py`；可选：`--users-per-district`、`--start`、`--end`、`--seed`、`--output`。 |
| `generate_sleep_map_aggregated.py` | 从 `beijing_sleep_map_multi_user.json` **聚合**出：`somni_sleep_analysis.json`（个人×日，含 `is_env_sensitive` 等修正）与 `somni_sleep_district.json`（区×日聚合）。 | `python scripts/generate_data/generate_sleep_map_aggregated.py`；可选：`--input`、`--out-analysis`、`--out-district`、`--start`、`--end`、`--seed`。 |
| `generate_heatmap_users.py` | 为睡眠**热力图**批量生成**虚拟用户**的 N 天数据：health / environment / vitals / sleep_events / sleep_map_score，并写 `output/heatmap_users_index.json` 索引。 | `python scripts/generate_data/generate_heatmap_users.py`；可选：`--count`、`--days`、`--start-date`、`--config`、`--seed`。 |

---

## 问卷与人格档案（Mongo / 翻译）

| 文件 | 作用 | 调用方式 |
|------|------|----------|
| `generate_survey_structures_with_bound_ids.py` | 连接 MongoDB，针对指定问卷 code（如 `somni_vip`、`somni_001`），处理问卷结构、题目绑定 ID，并结合中英文案映射输出到 `output/`（如 `quiz_questions.json` 等）。 | `python scripts/generate_data/generate_survey_structures_with_bound_ids.py`（**无 CLI 参数**；连接串与问卷 code 在脚本/环境变量中配置）。 |
| `generate_quiz_personalities_language_json.py` | 处理 `quiz_personalities` 集合：写 `language=zh`、导出中文 JSON，再翻译并写 `language=en`，输出 `quiz_personalities_zh.json` / `en.json`。 | `python scripts/generate_data/generate_quiz_personalities_language_json.py`（**无 CLI 参数**）。 |
| `generate_personality_profiles.py` | 为 **8 种睡眠人格 × 4 个第四维编码** 共 32 条生成人格配置文档，输出 `output/all_personality_profiles.json`（可写回 Mongo）。 | `python scripts/generate_data/generate_personality_profiles.py`（**无 CLI 参数**）。 |
| `generate_personality_data.py` | 为每种睡眠人格生成简化的**健康数据样例**（8×4=32 条），输出 `output/all_personality_health_data.json`；依赖项目中的 `personality_profile` 等模块。 | `python scripts/generate_data/generate_personality_data.py`（**无 CLI 参数**）。 |

---

## 使用提示

1. **工作目录**：请在仓库根目录执行上文 `python scripts/generate_data/...` 命令。  
2. **依赖顺序**：例如「先 health → 再 environment/vitals → 再 sleep_events → 再 apply_event_impacts」的流水线需按数据依赖顺序运行。  
3. **敏感配置**：含 MongoDB URI、翻译或 LLM 的脚本请通过 `.env` 管理，勿将密钥提交到版本库。

若本目录增删文件，请同步更新本 README 的表格与说明。
