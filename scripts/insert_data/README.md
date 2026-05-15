# `scripts/insert_data` 说明

本目录脚本用于把项目 `output/`（或其它路径）里的 JSON 写入 MongoDB。多数脚本会 `chdir` 到**仓库根目录**，因此推荐在**项目根**执行下面的命令。

## 环境与连接

- 在仓库根目录配置 `.env`，常用变量：
  - `MONGODB_URI` 或 `MONGO_URI`：Mongo 连接串（部分脚本若未配置会使用代码内默认 URI，以各脚本为准）。
  - `MONGODB_DB`：数据库名（可选；部分脚本会从 URI 路径解析库名）。
- 依赖：`pymongo`、`python-dotenv` 等（与主项目一致）。

## 脚本一览

| 文件 | 作用简述 |
|------|----------|
| `insert_somni_records.py` | 按「数据类型」扫描 `output/*_{类型}.json`，做日期字段解析后写入对应集合；`sleep_report` 为按 `uid` + `record_date` + `language` upsert。 |
| `insert_vitals_file_to_mongo.py` | 将指定单个 vitals JSON 文件批量 `insert_many` 到生理数据集合（默认可改集合名）。 |
| `insert_sleep_intervention_schemes.py` | 将睡眠干预方案 JSON（默认 `output/sleep_intervention_schemes.json`）按 `mhr_codes` 整组 upsert 到 `somni_temp_plans`（可改集合）。 |
| `insert_sleep_intervention_schemes_interv.py` | 将干预专用 JSON（默认 `output/sleep_intervention_schemes_interv.json`）按 `mhr_codes` upsert 到 `somni_temp_plans`；`create_time` / `update_time` 转 BSON 日期。 |
| `insert_quiz_surveys.py` | 从固定两个英文问卷 JSON upsert 到 `quiz_surveys`（按 `code` + `language`）。 |
| `insert_quiz_questions.py` | 将 `output/quiz_questions_translated_by_id_pairs.json` 插入 `quiz_questions`（保留 `_id` 为 ObjectId）。 |
| `insert_quiz_personalities_lang.py` | 将中/英人格 JSON 插入 `quiz_personalities`（去掉 `_id`，可选只插 zh/en）。 |
| `insert_personality_profiles.py` | 将 `output/all_personality_profiles.json` 插入 `quiz_personalities`（去掉 `_id`）。 |

以下分文件说明输入、目标集合与调用方式。

---

### `insert_somni_records.py`

**作用**：在 `output/` 下查找文件名包含 `_{data_type}.json` 的文件（例如 `xxx_health_data.json`），读取 JSON（单对象或数组），转换配置的日期字段与 `_id` 后写入 Mongo。

**当前支持的数据类型**（与脚本内 `aaa` 字典一致）及目标集合：

| 参数 `data_type` | Mongo 集合 |
|------------------|------------|
| `health_data` | `somni_records` |
| `calendar_events` | `somni_schedules` |
| `vitals_data` | `somni_physiological_data` |
| `daily_emotion_steps` | `somni_fusion` |
| `sleep_art_data` | `somni_dream_universe_assets` |
| `sleep_district` | `somni_sleep_district` |
| `sleep_analysis` | `somni_sleep_analysis` |
| `sleep_events` | `somni_events` |
| `sleep_report` | `somni_reports`（upsert，非纯插入） |
| `environment_data` | `somni_environment_data` |

**注意**：

- 目标集合**必须已存在**，否则脚本会跳过并打印「集合不存在」。
- 除 `sleep_report` 外，一般为 `insert_many`；`sleep_report` 按 `uid`、`record_date`、`language` 做 `UpdateOne` upsert。

**调用**（在项目根目录）：

```bash
python scripts/insert_data/insert_somni_records.py health_data
python scripts/insert_data/insert_somni_records.py sleep_report
python scripts/insert_data/insert_somni_records.py all
```

第二个参数为上面某一 `data_type`，或 `all` 依次处理所有类型。

---

### `insert_vitals_file_to_mongo.py`

**作用**：把**一个** vitals JSON（数组或单对象）规范化日期字段后，按批次 `insert_many` 写入集合，默认 `somni_physiological_data`。

**调用**（在项目根目录）：

```bash
python scripts/insert_data/insert_vitals_file_to_mongo.py
python scripts/insert_data/insert_vitals_file_to_mongo.py --file output/某用户_vitals_data.json
python scripts/insert_data/insert_vitals_file_to_mongo.py --file path/to/file.json --collection somni_physiological_data --batch-size 1000
python scripts/insert_data/insert_vitals_file_to_mongo.py --uri "$MONGODB_URI" --db Fullive
```

`--file` 为相对路径时，相对于**项目根**。未传 `--uri` 时读环境变量 `MONGODB_URI` / `MONGO_URI`。

---

### `insert_sleep_intervention_schemes.py`

**作用**：读取睡眠干预方案 JSON（数组或单对象），校验每条含有效 `mhr_codes`（长度为 4 的列表），转换 `create_time` / `update_time` 后，按 `mhr_codes` **upsert** 到 Mongo。

**默认**：数据文件为项目根下 `output/sleep_intervention_schemes.json`，集合为 `somni_temp_plans`。**必须在 `.env` 中配置 `MONGODB_URI` 或 `MONGO_URI`**（脚本不设默认密钥）。

**调用**：

```bash
python scripts/insert_data/insert_sleep_intervention_schemes.py
python scripts/insert_data/insert_sleep_intervention_schemes.py --file output/sleep_intervention_schemes.json
python scripts/insert_data/insert_sleep_intervention_schemes.py --collection somni_temp_plans
python scripts/insert_data/insert_sleep_intervention_schemes.py --dry-run
```

---

### `insert_sleep_intervention_schemes_interv.py`

**作用**：与 `insert_sleep_intervention_schemes.py` 相同写入策略，默认读取 **`output/sleep_intervention_schemes_interv.json`**（`type` 为 `interv`、phases 已与 `AI对话方案.md` 对齐的版本），将 **`create_time` / `update_time`** 转为 `datetime` 后 upsert 到 **`somni_temp_plans`**。

**调用**：

```bash
python scripts/insert_data/insert_sleep_intervention_schemes_interv.py
python scripts/insert_data/insert_sleep_intervention_schemes_interv.py --file output/sleep_intervention_schemes_interv.json
python scripts/insert_data/insert_sleep_intervention_schemes_interv.py --dry-run
```

**说明**：过滤键仍为 `mhr_codes` 单字段；若库中已有同 `mhr_codes` 的 `init` 方案，执行本脚本会用 **interv** 文档覆盖该键对应文档。

---

### `insert_quiz_surveys.py`

**作用**：读取写死的两个文件：`output/generated_somni_001_en.json`、`output/generated_somni_vip_en.json`，按文档中的 `code` 与 `language` 字段 **upsert** 到 `quiz_surveys`。

**调用**（无 CLI 参数）：

```bash
python scripts/insert_data/insert_quiz_surveys.py
```

---

### `insert_quiz_questions.py`

**作用**：读取 `output/quiz_questions_translated_by_id_pairs.json`，将字符串 `_id` 转为 `ObjectId`，时间字段转 `datetime`，**insert_many** 到 `quiz_questions`。

**调用**：

```bash
python scripts/insert_data/insert_quiz_questions.py
```

---

### `insert_quiz_personalities_lang.py`

**作用**：从 `output/quiz_personalities_zh.json` 和/或 `output/quiz_personalities_en.json` 读取记录，去掉 `_id`，转换时间后 **insert_many** 到 `quiz_personalities`。

**调用**：

```bash
python scripts/insert_data/insert_quiz_personalities_lang.py
python scripts/insert_data/insert_quiz_personalities_lang.py --source zh
python scripts/insert_data/insert_quiz_personalities_lang.py --source en
python scripts/insert_data/insert_quiz_personalities_lang.py --source both
```

---

### `insert_personality_profiles.py`

**作用**：读取 `output/all_personality_profiles.json`，去掉 `_id`，时间转 `datetime`，**insert_many** 到 `quiz_personalities`。

**调用**：

```bash
python scripts/insert_data/insert_personality_profiles.py
```

**说明**：与 `insert_quiz_personalities_lang.py` 写入**同一集合** `quiz_personalities`，用途不同（整包画像 vs 中英分文件）。重复执行会产生多条新文档，请按需清理或改脚本策略。

---

## 与脚本顶部注释不一致之处

`insert_somni_records.py` 文件头部注释里列出的部分子命令（如 `quiz_result`、`schedule_data` 等）**在当前 `aaa` 配置中已不存在**；请以本文档「支持的数据类型」表为准。若需恢复那些类型，要在脚本中重新加入对应配置。
