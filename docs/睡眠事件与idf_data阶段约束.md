# 睡眠事件与 `idf_data` 阶段约束

> **版本**：1.0  
> **适用范围**：`generate_health_data.generate_sleep_events` 初生成 + `generate_sleep_events_by_persona._rebalance_one_night_events` 人格再平衡  
> **相关文档**：`docs/睡眠事件睡眠阶段.md`（事件—体征/噪声范围）、`docs/reference/数据文件结构表.md`（`idf_data` 字段）

---

## 1. `idf_data` 是什么

每条 `health_data` 记录含 `idf_data[]`，按时间顺序描述**当夜睡眠分期**，每段形如：

```json
{ "stage": "awake", "start": "00:10", "end": "00:33" }
```

| 字段 | 说明 |
|------|------|
| `stage` | `awake` \| `light` \| `deep` \| `rem` |
| `start` / `end` | 当日本地 `HH:MM`（与 `raw_data.sleep_time` / `wake_time` 墙钟对齐，跨午夜由生成逻辑处理） |

典型一夜结构：**首段清醒（卧床/入睡潜伏期）→ 多周期 light/deep/rem → 末段清醒（晨起）**，中间可能插入**半夜觉醒**的 `awake` 段。

事件 `event_timestamp`（`HH:MM`）必须落在对应约束段的 **`[start, end)`** 内（实现上允许段内随机分钟，并再与 `sleep_time`～`wake_time` 睡眠窗求交）。

---

## 2. 阶段语义（生成约束用）

文档与代码里会区分三类「清醒」，不要混用：

| 术语 | 定义 | 典型用途 |
|------|------|----------|
| **首段清醒** | `idf_data[0]` 且 `stage == "awake"` | **入睡困难**（入睡潜伏期） |
| **半夜觉醒清醒** | 索引 `1 … len-2` 且 `stage == "awake"` | 异常体动、咳嗽、体位切换等「夜醒后」事件 |
| **任意非首段清醒** | 所有 `awake` 段（含首段与半夜） | 突发噪声、部分 persona 再平衡规则 |

**非清醒睡眠段**：`light` / `deep` / `rem` — 打鼾、噩梦、持续噪声、梦话等。

---

## 3. 事件 — 阶段约束总表

下表为**目标约束**（产品/生理语义）。`event_type` 为展示名，`code` 为存储码，`type` 为 `abnormal` / `normal` / `intervention`。

### 3.1 异常事件（`type: abnormal`）

| event_type | code | 允许 `stage` | idf 段级约束 | 附加条件 |
|------------|------|--------------|--------------|----------|
| 入睡困难 | `sleeping` | `awake` | **仅首段清醒** `idf_data[0]` | `raw_data.sleep_latency` > 30（可配置）；锚点宜在首段前 35% 或前 8 分钟内 |
| 噩梦应激 | `nightmare` | `rem`（优先） | 任意 REM 段；每晚至多 1 条主事件 | 偏好 01:00–04:00 与睡眠窗交集 |
| 心率上升 | `heart_rate_increase` | `rem` | 须落在 REM；且时刻 **早于** 当夜 `nightmare` 1.25–5 分钟（可扩至约 12 分钟） | 不单独随机生成；仅噩梦联动（约 40%） |
| 异常体动 | `movement` | `light`、`deep`、`rem` | 任意匹配的非清醒段 | **禁止** `awake`（含首段与半夜觉醒） |
| 家电持续声 | `appliance_continuous` | `light`、`deep`、`rem` | 任意匹配段 | persona 再平衡可含 `awake`（见 §5） |
| 环境持续声 | `environment_continuous` | 同上 | 同上 | 同上 |
| 邻里持续声 | `neighbor_continuous` | 同上 | 同上 | 同上 |
| 自然持续声 | `nature_continuous` | 同上 | 同上 | 同上 |
| 突发撞击声 | `sudden_impact` | `light`、`rem`（**少** `deep`） | 任意匹配段 | 深睡觉醒阈值高，深睡仅低概率 |
| 突发交通声 | `sudden_traffic` | 同上 | 同上 | 同上 |
| 人声/门铃声 | `voice_doorbell` | 同上 | 同上 | 同上 |
| 自然突发声 | `nature_sudden` | 同上 | 同上 | 同上 |
| 物品突发声 | `object_sudden` | 同上 | 同上 | 同上 |
| 噪声事件 | （聚合标签） | `light`、`awake` | 实现中作类别占位 | 具体子类型见上表 |

### 3.2 正常事件（`type: normal`）

| event_type | code | 允许 `stage` | idf 段级约束 | 备注 |
|------------|------|--------------|--------------|------|
| 打鼾 | `snoring` | `light`、`deep`、`rem` | **禁止** `awake`；不早于**首个浅睡段结束** | persona 流水线：多段连续事件，按 `sleep_time`～`wake_time` 窗放置（须与 idf 非清醒段一致） |
| 梦话 | `sleep_talking` | `light`、`rem` | 任意匹配段 | |
| 咳嗽 | `cough` | `light`、`awake` | `awake` = 半夜觉醒清醒 | |
| 睡眠姿势切换 | `posture_switch` | `light`、`awake` | 半夜觉醒清醒 | |
| 肢体动作 | `body_movement` / `limb_movements` | `light`、`deep`（可略 `rem`） | 浅睡为主 | |
| 单次体动 | `single_movement` / `once_movement` | `light`、`awake` | 半夜觉醒或浅睡 | |
| 自然微动 | `micro_movement` / `natural_movement` | `light`、`deep`、`rem` | 全夜可稀疏分布 | |
| 呼吸声 | `breathing` | `light`、`deep`、`rem` | 全夜 | |
| 吞咽 | `swallow` | `light`、`awake` | 半夜觉醒或浅睡 | |
| 安静 | `silence` | `light`、`deep` | REM 较少表现为「绝对安静」 | 见 `docs/睡眠事件阶段推荐与数据范围修订建议.md` |

### 3.3 干预事件（`type: intervention`）

| event_type | code | 阶段约束 | 说明 |
|------------|------|----------|------|
| AI主动干预 | 与父异常事件 `code` 相同 | **继承父事件** 所在段；时刻通常为父事件 +约 2 分钟 | 每条 `abnormal` 主事件最多 1 条配对干预；不计入「每分期每晚 1 条」配额 |

---

## 4. 特殊规则（必读）

### 4.1 入睡困难 — 仅首段清醒

- **必须**：`idf_data[0].stage == "awake"`，且 `event_timestamp` ∈ 首段 `[start, end]` ∩ `[sleep_time, wake_time]`。
- **禁止**：落在第二段及以后的 `awake`（半夜觉醒）或任何 `light/deep/rem`。
- **不宜**：落在首段清醒的后半段（实现上偏向首段前 35% 或前 8 分钟）。
- **数据门控**：`sleep_latency > 30`（默认）；人格再平衡还可由「潜伏期>30 / 呼吸率 5 分钟>15 / 翻身>5」OR 触发。

### 4.2 噩梦 + 心率上升

- 噩梦：锚在 **REM**。
- 心率上升：仅作为噩梦的伴随事件，锚在**同一夜、更早**的 REM 子窗口内。

### 4.3 「半夜觉醒」类 awake

下列事件的 `awake` **不是**首段卧床，而是 `_middle_awake_windows_from_idf`（索引 `1 … n-2` 的 `awake`）：

- `posture_switch`、`cough` / `cough_clearing`、`swallow`  
- `once_movement` / `single_movement`  

**异常体动**不在此列：仅允许 `light` / `deep` / `rem`。

若整夜无半夜 `awake` 段，上述「半夜觉醒类」事件可回退到 `light`（或仅浅睡），**仍不得**用首段清醒替代「夜醒体动」。

### 4.4 打鼾

- 不得在 `awake` 段。  
- 有 `idf_data` 时，锚点须投影到 `light|deep|rem` 并集。  
- persona 脚本在 `apnea_count >= 5` 时生成多段连续打鼾，段起点应在睡眠窗内且不与已用区间重叠（实现层宜再校验是否落在非清醒 idf 段）。

### 4.5 每晚分期配额（`generate_sleep_events`）

| 分期 | 每晚主事件锚点上限（不含 AI 干预） |
|------|-----------------------------------|
| `light` | 1 |
| `deep` | 1 |
| `rem` | 2（含噩梦 + 联动心率） |
| `awake` | 1（入睡困难占 awake 配额；半夜觉醒类与其互斥策略见代码） |

异常主事件总数、正常主事件总数另有全局上限（默认 abnormal 5 / normal 8，persona 再平衡可更严）。

---

## 5. 两条流水线差异

| 步骤 | 模块 | 阶段规则来源 |
|------|------|----------------|
| 初生成 | `generate_health_data.generate_sleep_events` | `_SLEEP_EVENT_STAGE_PREFERENCES`、`eligible_sleeping_windows`、`_middle_awake_windows_from_idf` |
| 人格再平衡 | `generate_sleep_events_by_persona._rebalance_one_night_events` | `_EVENT_STAGE_ALLOWED`（按 `event_type` 中文名）+ 概率/保险丝 |

`generate_sleep_events_by_persona.py` 中 `_EVENT_STAGE_ALLOWED` 与上表对齐情况：

| event_type | `_ABNORMAL_EVENT_STAGE_ALLOWED` | 落点函数 |
|------------|--------------------------------|----------|
| 入睡困难 | `{awake}` | `_pick_minute_in_first_onset_awake`（仅 `idf_data[0]`） |
| 噩梦应激 / 心率上升 / 异常体动 | 与 §3 一致 | `_pick_minute_in_stage_windows` |
| 持续/突发噪声 | `light,deep,rem` 或 `light,rem` | 同上；读 `start`/`end` |

正常事件（打鼾、梦话等）仍用 `_EVENT_STAGE_ALLOWED` 默认值，**不在此表维护**。

---

## 6. 校验清单（实现 / 联调）

生成或写回事件后，对每条主事件建议检查：

1. `event_timestamp` 解析为分钟后，落在某一 `idf_data` 段的 `[start, end)` 且 `stage` 属于上表「允许 stage」。  
2. **入睡困难**：段索引必须为 `0`，且 `stage == awake`。  
3. **心率上升**：存在当夜 `nightmare` 且时间早于噩梦。  
4. **AI主动干预**：`related_event_id` 指向父异常事件，时刻在父事件之后、仍在睡眠窗内。  
5. 与 `docs/睡眠事件睡眠阶段.md` 中的噪声 dB、体征范围一致（阶段约束 + 数值范围）。

---

## 7. 代码索引

| 符号 / 函数 | 文件 |
|-------------|------|
| `_SLEEP_EVENT_STAGE_PREFERENCES` | `scripts/generate_data/generate_health_data.py` |
| `eligible_sleeping_windows` | 同上（首段 `awake`） |
| `_middle_awake_windows_from_idf` | 同上（半夜觉醒） |
| `_EVENT_STAGE_ALLOWED`、`_pick_minute_in_stage_windows` | `scripts/generate_data/generate_sleep_events_by_persona.py` |
| `generate_sleep_events` 文档字符串 | `generate_health_data.py`（入睡困难 / 噩梦 / 打鼾规则摘要） |

---

*后续若收紧 persona 再平衡或修复 `start`/`end` 字段读取，请同步更新本节 §5 差异表。*
