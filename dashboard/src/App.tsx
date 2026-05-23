import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'

type Mode = 'vitals' | 'environment' | 'sleep' | 'ranking'

type VitalsRecord = {
  record_date: string
  collected_at: string
  metrics: { heart_rate?: number; respiration_rate?: number }
}

type EnvironmentRecord = {
  record_date: string
  collected_at: string
  temperature?: number
  noise?: number
}

type SleepStageItem = { stage: 'awake' | 'light' | 'deep' | 'rem'; start: string; end: string }
type HealthRecord = { record_date: string; data_label?: 'good' | 'bad' | string; idf_data: SleepStageItem[] }
type SleepEventRecord = {
  record_date: string
  event_timestamp: string
  event_type: string
  type: string
  detail?: { result_summary?: string }
}
type UserManifestItem = { uid: string }

type SleepBubblePoint = [number, number, string, string, string]
type SleepBubbleFormatterParam = { data?: SleepBubblePoint }
type CustomRenderApi = {
  value: (index: number) => unknown
  coord: (value: [number, number]) => [number, number]
  style: (style: Record<string, unknown>) => Record<string, unknown>
}

const STAGE_LEVEL: Record<SleepStageItem['stage'], number> = { awake: 3, light: 2, rem: 1, deep: 0 }
const STAGE_COLOR: Record<SleepStageItem['stage'], string> = {
  awake: '#f5a55a',
  light: '#6f4cff',
  rem: '#b54566',
  deep: '#5562e9',
}

const PERSONA_OPTIONS = [
  { uid: '69aea593af5e6cbf08027964', name: '完美主义百灵鸟' },
  { uid: '69aea63eaf5e6cbf08027965', name: '敏感的晨间鹿' },
  { uid: '69aea6d8af5e6cbf08027966', name: '效率至上考拉' },
  { uid: '69aea6e3af5e6cbf08027967', name: '阳光漫步者' },
  { uid: '69aea6e8af5e6cbf08027968', name: '深夜灵感守望者' },
  { uid: '69aea6eeaf5e6cbf08027969', name: '深海独奏家' },
  { uid: '69aea6f3af5e6cbf0802796a', name: '创意夜猫子' },
  { uid: '69aea6f8af5e6cbf0802796b', name: '月光冲浪者' },
]

const PERSONA_UIDS = new Set<string>(PERSONA_OPTIONS.map((p) => p.uid))

function getPersonaName(uid: string): string {
  return PERSONA_OPTIONS.find((p) => p.uid === uid)?.name ?? uid
}

function getHealthDataLabelText(label: string | undefined): string | null {
  if (label === 'good') return '好数据'
  if (label === 'bad') return '坏数据'
  return null
}

type SleepAnalysisRecord = {
  uid: string
  stats_date: string
  user_name: string
  score: number
  sleep_seconds: number
  deep_sleep_seconds: number
  dimensions?: {
    deep_sleep?: { score: number }
    sleep_duration?: { score: number }
    sleep_efficiency?: { score: number }
    abnormal_events?: { score: number }
    routine_regularity?: { score: number }
  }
}

const baseChartStyle = {
  backgroundColor: 'transparent',
  grid: { left: 56, right: 28, top: 46, bottom: 48 },
  tooltip: { trigger: 'axis' as const, backgroundColor: '#10182f', borderColor: '#2d4677' },
  xAxis: {
    type: 'time' as const,
    axisLine: { lineStyle: { color: 'rgba(145,170,220,0.35)' } },
    axisLabel: { color: '#a6b4de' },
    splitLine: { show: true, lineStyle: { color: 'rgba(145,170,220,0.1)' } },
  },
  yAxis: {
    type: 'value' as const,
    axisLine: { show: false },
    axisLabel: { color: '#a6b4de' },
    splitLine: { lineStyle: { color: 'rgba(145,170,220,0.1)' } },
  },
}

function parseClock(date: string, clock: string): number {
  const [h, m] = clock.split(':').map(Number)
  const base = new Date(`${date}T00:00:00`)
  if (Number.isNaN(h) || Number.isNaN(m)) return base.getTime()
  const dt = new Date(base)
  dt.setHours(h, m, 0, 0)
  if (h < 12) dt.setDate(dt.getDate() + 1)
  return dt.getTime()
}

function formatTime(ts: number): string {
  return new Date(ts).toTimeString().slice(0, 5)
}

function formatSeconds(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return `${h}h ${m}m`
}

function fetchJsonNoStore<T>(url: string): Promise<T> {
  const stamp = Date.now()
  const nextUrl = `${url}${url.includes('?') ? '&' : '?'}t=${stamp}`
  return fetch(nextUrl, { cache: 'no-store' }).then((res) => res.json())
}

function buildSleepEventPoints(selectedDate: string, sleepEvents: SleepEventRecord[], y: number): SleepBubblePoint[] {
  return sleepEvents
    .filter((x) => x.record_date === selectedDate)
    .map(
      (x): SleepBubblePoint => [parseClock(selectedDate, x.event_timestamp), y, x.event_type, x.type, x.detail?.result_summary ?? ''],
    )
    .sort((a, b) => Number(a[0]) - Number(b[0]))
}

const RANKING_TOP_N = 100

/** 当日：全部虚拟用户 + 当前人格一条，按综合分降序取前 N 名 */
function buildDailyRanking(
  virtualRecords: SleepAnalysisRecord[],
  personaRecord: SleepAnalysisRecord | undefined,
  topN = RANKING_TOP_N,
): SleepAnalysisRecord[] {
  const byUid = new Map<string, SleepAnalysisRecord>()
  for (const r of virtualRecords) {
    byUid.set(r.uid, r)
  }
  if (personaRecord) {
    byUid.set(personaRecord.uid, personaRecord)
  }
  return Array.from(byUid.values())
    .sort((a, b) => b.score - a.score)
    .slice(0, topN)
}

function buildSleepEventSeries(data: SleepBubblePoint[], color: string) {
  return {
    type: 'scatter' as const,
    name: '睡眠事件',
    symbol: 'ellipse',
    symbolSize: [52, 36],
    itemStyle: {
      color,
      borderColor: '#c6d1ff',
      borderWidth: 1,
      shadowColor: color,
      shadowBlur: 14,
    },
    label: { show: true, color: '#e9edff', formatter: (p: SleepBubbleFormatterParam) => String(p.data?.[2] ?? '') },
    tooltip: {
      formatter: (p: SleepBubbleFormatterParam) => `${p.data?.[2]}<br/>${p.data?.[3]}<br/>${p.data?.[4] || '无说明'}`,
    },
    data,
  }
}

function RankingView({
  personaUid,
  onPersonaChange,
  dataRefreshKey,
}: {
  personaUid: string
  onPersonaChange: (uid: string) => void
  dataRefreshKey: number
}) {
  const [virtualData, setVirtualData] = useState<SleepAnalysisRecord[]>([])
  const [personaData, setPersonaData] = useState<SleepAnalysisRecord[]>([])
  const [healthData, setHealthData] = useState<HealthRecord[]>([])
  const [selectedDate, setSelectedDate] = useState('')
  const [poolLoading, setPoolLoading] = useState(true)
  const [personaLoading, setPersonaLoading] = useState(false)

  const virtualPool = useMemo(
    () => virtualData.filter((r) => !PERSONA_UIDS.has(r.uid)),
    [virtualData],
  )

  useEffect(() => {
    setPoolLoading(true)
    fetchJsonNoStore<SleepAnalysisRecord[]>('/data/ranking/somni_sleep_analysis.json')
      .then((virtual) => {
        setVirtualData(virtual)
      })
      .finally(() => setPoolLoading(false))
  }, [dataRefreshKey])

  useEffect(() => {
    if (!personaUid) {
      setPersonaData([])
      setHealthData([])
      return
    }
    setPersonaLoading(true)
    Promise.all([
      fetchJsonNoStore<SleepAnalysisRecord[]>(`/data/ranking/${personaUid}_somni_sleep_analysis.json`),
      fetchJsonNoStore<HealthRecord[]>(`/data/users/${personaUid}_health_data.json`),
    ])
      .then(([persona, health]) => {
        setPersonaData(persona)
        setHealthData(health)
      })
      .catch(() => {
        setPersonaData([])
        setHealthData([])
      })
      .finally(() => setPersonaLoading(false))
  }, [personaUid, dataRefreshKey])

  const availableDates = useMemo(() => {
    const virtualDates = new Set(virtualPool.map((r) => r.stats_date))
    if (!personaData.length) return Array.from(virtualDates).sort()
    const personaDates = new Set(personaData.map((r) => r.stats_date))
    return Array.from(virtualDates).filter((d) => personaDates.has(d)).sort()
  }, [virtualPool, personaData])

  useEffect(() => {
    if (availableDates.length > 0 && !availableDates.includes(selectedDate)) {
      setSelectedDate(availableDates[0])
    }
  }, [availableDates, selectedDate])

  const rankingMeta = useMemo(() => {
    if (!selectedDate) {
      return { ranked: [] as SleepAnalysisRecord[], virtualCount: 0, totalCount: 0, hasPersona: false }
    }
    const dateVirtual = virtualPool.filter((r) => r.stats_date === selectedDate)
    const personaRec = personaData.find((r) => r.stats_date === selectedDate)
    const ranked = buildDailyRanking(dateVirtual, personaRec)
    const totalCount = dateVirtual.length + (personaRec ? 1 : 0)
    return {
      ranked,
      virtualCount: dateVirtual.length,
      totalCount,
      hasPersona: Boolean(personaRec),
    }
  }, [virtualPool, personaData, selectedDate])

  const { ranked, virtualCount, totalCount, hasPersona } = rankingMeta

  const personaRank = useMemo(() => {
    const idx = ranked.findIndex((r) => r.uid === personaUid)
    return idx >= 0 ? idx + 1 : null
  }, [ranked, personaUid])

  const personaScore = useMemo(() => {
    return ranked.find((r) => r.uid === personaUid)?.score ?? null
  }, [ranked, personaUid])

  const personaHealthLabel = useMemo(() => {
    if (!selectedDate) return undefined
    return healthData.find((h) => h.record_date === selectedDate)?.data_label
  }, [healthData, selectedDate])

  const personaHealthLabelText = getHealthDataLabelText(personaHealthLabel)

  const handleDateChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedDate(e.target.value)
  }, [])

  const handlePersonaChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      onPersonaChange(e.target.value)
    },
    [onPersonaChange],
  )

  const loading = poolLoading || personaLoading

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#a6b4de' }}>加载中...</div>
  }

  return (
    <>
      <div style={{ marginBottom: 16, padding: '10px 14px', background: 'rgba(255,255,255,0.04)', borderRadius: 8, fontSize: 13, color: '#8892b0', lineHeight: 1.6 }}>
        综合分 = 深睡(25%) + 时长(25%) + 事件(20%) + 效率(15%) + 规律(15%)。
        深睡按当日占比评分；时长按当日总睡眠 4h~11h 连续曲线；效率按入睡潜伏期；事件按异常中断次数与时长；规律按近 14 日入睡/起床时刻波动。
      </div>
      <div className="ranking-controls">
        <label className="ranking-control">
          <span className="ranking-control-label">人格</span>
          <select value={personaUid} onChange={handlePersonaChange}>
            {PERSONA_OPTIONS.map((p) => (
              <option key={p.uid} value={p.uid}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="ranking-control">
          <span className="ranking-control-label">日期</span>
          <select value={selectedDate} onChange={handleDateChange}>
            {availableDates.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </label>
        {selectedDate && (
          <div className="ranking-summary">
            <span>
              当日共 {totalCount} 人参与排名（虚拟 {virtualCount}
              {hasPersona ? ' + 人格 1' : ''}），展示前 {Math.min(RANKING_TOP_N, totalCount)} 名
            </span>
            {hasPersona && personaRank != null && (
              <span className="ranking-persona-stat">
                「{getPersonaName(personaUid)}」综合分 {personaScore ?? '-'}，排名第 {personaRank} / {totalCount}
                {personaHealthLabelText && (
                  <> · 参与计算：<span className={`health-label-badge health-label-${personaHealthLabel}`}>{personaHealthLabelText}</span></>
                )}
              </span>
            )}
          </div>
        )}
      </div>
      {ranked.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '48px', color: '#8892b0' }}>该日期暂无排名数据</div>
      ) : (
      <div className="ranking-table-wrap">
        <table className="ranking-table">
          <thead>
            <tr>
              <th className="rank-col">排名</th>
              <th>用户名</th>
              <th className="num-col">分数</th>
              <th className="num-col">深睡时长</th>
              <th className="num-col">总睡眠时长</th>
              <th className="num-col">深睡维度</th>
              <th className="num-col">时长维度</th>
              <th className="num-col">效率维度</th>
              <th className="num-col">事件维度</th>
              <th className="num-col">规律维度</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((r, i) => {
              const isPersona = r.uid === personaUid
              return (
                <tr key={`${r.uid}-${i}`} className={isPersona ? 'persona-row' : ''}>
                  <td className="rank-col">{i + 1}</td>
                  <td className="name-cell">
                    <span className={isPersona ? 'persona-name' : ''}>
                      {r.user_name}
                    </span>
                    {isPersona && <span className="persona-badge">人格</span>}
                    {isPersona && personaHealthLabelText && (
                      <span className={`health-label-badge health-label-${personaHealthLabel}`}>
                        {personaHealthLabelText}
                      </span>
                    )}
                  </td>
                  <td className="num-col score-cell">{r.score}</td>
                  <td className="num-col">{formatSeconds(r.deep_sleep_seconds)}</td>
                  <td className="num-col">{formatSeconds(r.sleep_seconds)}</td>
                  <td className="num-col">{r.dimensions?.deep_sleep?.score ?? '-'}</td>
                  <td className="num-col">{r.dimensions?.sleep_duration?.score ?? '-'}</td>
                  <td className="num-col">{r.dimensions?.sleep_efficiency?.score ?? '-'}</td>
                  <td className="num-col">{r.dimensions?.abnormal_events?.score ?? '-'}</td>
                  <td className="num-col">{r.dimensions?.routine_regularity?.score ?? '-'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      )}
    </>
  )
}

function App() {
  const [mode, setMode] = useState<Mode>('vitals')
  const [users, setUsers] = useState<UserManifestItem[]>([])
  const [selectedUser, setSelectedUser] = useState('')
  const [vitals, setVitals] = useState<VitalsRecord[]>([])
  const [environment, setEnvironment] = useState<EnvironmentRecord[]>([])
  const [health, setHealth] = useState<HealthRecord[]>([])
  const [sleepEvents, setSleepEvents] = useState<SleepEventRecord[]>([])
  const [selectedDate, setSelectedDate] = useState('')
  const [labelFilter, setLabelFilter] = useState<'all' | 'good' | 'bad'>('all')
  const [dataRefreshKey, setDataRefreshKey] = useState(0)

  const refreshData = useCallback(() => {
    setDataRefreshKey((k) => k + 1)
  }, [])

  useEffect(() => {
    fetchJsonNoStore<UserManifestItem[]>('/data/users/manifest.json')
      .then((list: UserManifestItem[]) => {
        setUsers(list)
        setSelectedUser((prev) => (prev && list.some((u) => u.uid === prev) ? prev : (list[0]?.uid ?? '')))
      })
  }, [dataRefreshKey])

  useEffect(() => {
    if (!selectedUser) return
    Promise.all([
      fetchJsonNoStore<VitalsRecord[]>(`/data/users/${selectedUser}_vitals_data.json`),
      fetchJsonNoStore<EnvironmentRecord[]>(`/data/users/${selectedUser}_environment_data.json`),
      fetchJsonNoStore<HealthRecord[]>(`/data/users/${selectedUser}_health_data.json`),
      fetchJsonNoStore<SleepEventRecord[]>(`/data/users/${selectedUser}_sleep_events.json`),
    ]).then(([v, e, h, se]) => {
      setVitals(v)
      setEnvironment(e)
      setHealth(h)
      setSleepEvents(se)
      const allDates = Array.from(
        new Set<string>([...v.map((x: VitalsRecord) => x.record_date), ...h.map((x: HealthRecord) => x.record_date)]),
      ).sort()
      setSelectedDate(allDates[0] ?? '')
    })
  }, [selectedUser, dataRefreshKey])

  const dateLabelMap = useMemo(() => {
    const map = new Map<string, 'good' | 'bad' | string>()
    health.forEach((x) => {
      map.set(x.record_date, x.data_label ?? '')
    })
    return map
  }, [health])

  const dateOptions = useMemo(() => {
    const dates = new Set<string>()
    vitals.forEach((x) => dates.add(x.record_date))
    health.forEach((x) => dates.add(x.record_date))
    const sortedDates = Array.from(dates).sort()
    if (labelFilter === 'all') return sortedDates
    return sortedDates.filter((d) => dateLabelMap.get(d) === labelFilter)
  }, [vitals, health, labelFilter, dateLabelMap])

  const activeSelectedDate = dateOptions.includes(selectedDate) ? selectedDate : (dateOptions[0] ?? '')

  const option = useMemo(() => {
    if (!activeSelectedDate) return {}
    const dayLabelRaw = dateLabelMap.get(activeSelectedDate)
    const dayLabel = dayLabelRaw === 'good' || dayLabelRaw === 'bad' ? dayLabelRaw : 'unknown'
    const isGood = dayLabel === 'good'
    if (mode === 'vitals') {
      const dayData = vitals
        .filter((x) => x.record_date === activeSelectedDate)
        .map((x) => [new Date(x.collected_at).getTime(), x.metrics.heart_rate ?? null, x.metrics.respiration_rate ?? null])
        .sort((a, b) => Number(a[0]) - Number(b[0]))
      const vitalValues = dayData.flatMap((x) => [x[1], x[2]]).filter((x): x is number => x !== null)
      const eventY = (vitalValues.length ? Math.max(...vitalValues) : 0) + 6
      const events = buildSleepEventPoints(activeSelectedDate, sleepEvents, eventY)
      return {
        ...baseChartStyle,
        legend: { top: 10, textStyle: { color: '#c8d5ff' } },
        yAxis: { ...baseChartStyle.yAxis, name: 'bpm / rpm', nameTextStyle: { color: '#93a8da' } },
        series: [
          {
            name: '心率',
            type: 'line',
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 3, color: isGood ? '#31d08c' : '#ff6b81' },
            itemStyle: { color: isGood ? '#31d08c' : '#ff6b81' },
            data: dayData.map((x) => [x[0], x[1]]),
          },
          {
            name: '呼吸率',
            type: 'line',
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 3, color: isGood ? '#6ac7a0' : '#ff9b7b' },
            itemStyle: { color: isGood ? '#6ac7a0' : '#ff9b7b' },
            data: dayData.map((x) => [x[0], x[2]]),
          },
          buildSleepEventSeries(events, isGood ? '#4a94ff' : '#a772ff'),
        ],
      }
    }

    if (mode === 'environment') {
      const dayData = environment
        .filter((x) => x.record_date === activeSelectedDate)
        .map((x) => [new Date(x.collected_at).getTime(), x.temperature ?? null, x.noise ?? null])
        .sort((a, b) => Number(a[0]) - Number(b[0]))
      const envValues = dayData.flatMap((x) => [x[1], x[2]]).filter((x): x is number => x !== null)
      const eventY = (envValues.length ? Math.max(...envValues) : 0) + 4
      const events = buildSleepEventPoints(activeSelectedDate, sleepEvents, eventY)
      return {
        ...baseChartStyle,
        legend: { top: 10, textStyle: { color: '#c8d5ff' } },
        yAxis: { ...baseChartStyle.yAxis, name: '°C / dB', nameTextStyle: { color: '#93a8da' } },
        series: [
          {
            name: '温度',
            type: 'line',
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 3, color: isGood ? '#31d08c' : '#ff6b81' },
            itemStyle: { color: isGood ? '#31d08c' : '#ff6b81' },
            data: dayData.map((x) => [x[0], x[1]]),
          },
          {
            name: '噪音',
            type: 'line',
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 3, color: isGood ? '#6ac7a0' : '#ff9b7b' },
            itemStyle: { color: isGood ? '#6ac7a0' : '#ff9b7b' },
            data: dayData.map((x) => [x[0], x[2]]),
          },
          buildSleepEventSeries(events, isGood ? '#4a94ff' : '#a772ff'),
        ],
      }
    }

    if (mode === 'ranking') return {}

    const sleep = health.find((x) => x.record_date === activeSelectedDate)
    const stages = (sleep?.idf_data ?? [])
      .map((it) => [
        parseClock(activeSelectedDate, it.start),
        parseClock(activeSelectedDate, it.end),
        STAGE_LEVEL[it.stage],
        it.stage,
      ])
      .sort((a, b) => Number(a[0]) - Number(b[0]))
    const stageConnectors = stages.slice(1).map((current, index) => {
      const prev = stages[index]
      return [current[0], prev[2], current[2], prev[3], current[3]]
    })
    const events = buildSleepEventPoints(activeSelectedDate, sleepEvents, 3.3)

    const startTs = parseClock(activeSelectedDate, '22:00')
    const endTs = parseClock(activeSelectedDate, '07:00')

    return {
      backgroundColor: 'transparent',
      grid: { left: 50, right: 20, top: 40, bottom: 42 },
      tooltip: {
        trigger: 'item',
        backgroundColor: '#10182f',
        borderColor: '#2d4677',
        formatter: (params: { seriesName?: string; data?: unknown }) => {
          const data = params.data as (string | number)[] | undefined
          if (!data) return ''
          if (params.seriesName === '睡眠阶段') {
            const stageNameMap: Record<string, string> = {
              awake: '清醒',
              light: '浅睡',
              rem: 'REM',
              deep: '深睡',
            }
            return `${stageNameMap[String(data[3])] ?? data[3]}<br/>${formatTime(Number(data[0]))} - ${formatTime(Number(data[1]))}`
          }
          if (params.seriesName === '睡眠事件') {
            return `${data[2]}<br/>${formatTime(Number(data[0]))}<br/>${data[3]}<br/>${data[4] || '无说明'}`
          }
          return ''
        },
      },
      xAxis: {
        type: 'time',
        min: startTs,
        max: endTs,
        interval: 30 * 60 * 1000,
        axisPointer: {
          show: true,
          snap: true,
          lineStyle: { color: '#8cb2ff88', width: 1 },
          label: {
            show: true,
            formatter: (p: { value: number }) => formatTime(Number(p.value)),
          },
        },
        axisLabel: {
          color: '#a6b4de',
          formatter: (value: number) => formatTime(value),
        },
        splitLine: { show: true, lineStyle: { color: 'rgba(145,170,220,0.12)' } },
        axisLine: { lineStyle: { color: 'rgba(145,170,220,0.35)' } },
      },
      yAxis: {
        type: 'value',
        min: -0.5,
        max: 3.8,
        axisLabel: {
          color: '#a6b4de',
          formatter: (v: number) => ['深睡', 'REM', '浅睡', '清醒'][v] ?? '',
        },
        splitLine: { lineStyle: { color: 'rgba(145,170,220,0.12)' } },
      },
      series: [
        {
          type: 'custom',
          name: '阶段连接线',
          renderItem: (_params: unknown, api: CustomRenderApi) => {
            const x = Number(api.value(0))
            const fromY = Number(api.value(1))
            const toY = Number(api.value(2))
            const prevStage = api.value(3) as SleepStageItem['stage']
            const nextStage = api.value(4) as SleepStageItem['stage']
            const p1 = api.coord([x, fromY])
            const p2 = api.coord([x, toY])
            const y1 = p1[1]
            const y2 = p2[1]
            const top = Math.min(y1, y2)
            const height = Math.max(2, Math.abs(y2 - y1))
            const width = 10
            return {
              type: 'rect',
              shape: { x: p1[0] - width / 2, y: top, width, height, r: 6 },
              style: api.style({
                fill: {
                  type: 'linear',
                  x: 0,
                  y: 0,
                  x2: 0,
                  y2: 1,
                  colorStops:
                    y1 <= y2
                      ? [
                          { offset: 0, color: `${STAGE_COLOR[prevStage]}cc` },
                          { offset: 1, color: `${STAGE_COLOR[nextStage]}cc` },
                        ]
                      : [
                          { offset: 0, color: `${STAGE_COLOR[nextStage]}cc` },
                          { offset: 1, color: `${STAGE_COLOR[prevStage]}cc` },
                        ],
                },
                opacity: 0.9,
              }),
            }
          },
          encode: { x: 0, y: [1, 2] },
          data: stageConnectors,
          silent: true,
          z: 1,
        },
        {
          type: 'custom',
          name: '睡眠阶段',
          renderItem: (_params: unknown, api: CustomRenderApi) => {
            const start = api.coord([Number(api.value(0)), Number(api.value(2))])
            const end = api.coord([Number(api.value(1)), Number(api.value(2))])
            const height = 42
            const y = start[1] - height / 2
            const width = Math.max(6, end[0] - start[0])
            return {
              type: 'rect',
              shape: { x: start[0], y, width, height, r: 20 },
              style: api.style({
                fill: STAGE_COLOR[api.value(3) as SleepStageItem['stage']],
                opacity: 0.45,
                stroke: '#d2dcff33',
              }),
            }
          },
          encode: { x: [0, 1], y: 2 },
          data: stages,
          z: 2,
        },
        buildSleepEventSeries(events, '#6f79ff'),
      ],
    }
  }, [mode, activeSelectedDate, vitals, environment, health, sleepEvents, dateLabelMap])

  return (
    <main className="page">
      <header className="toolbar">
        <h1>睡眠与趋势看板</h1>
        <div className="actions">
          {mode !== 'ranking' && (
            <select value={selectedUser} onChange={(e) => setSelectedUser(e.target.value)}>
              {users.map((u) => (
                <option key={u.uid} value={u.uid}>
                  {getPersonaName(u.uid)}
                </option>
              ))}
            </select>
          )}
          {mode !== 'ranking' && (
            <>
              <select value={activeSelectedDate} onChange={(e) => setSelectedDate(e.target.value)}>
                {dateOptions.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              {(mode === 'vitals' || mode === 'environment') && (
                <select value={labelFilter} onChange={(e) => setLabelFilter(e.target.value as 'all' | 'good' | 'bad')}>
                  <option value="all">全部标签</option>
                  <option value="good">仅好数据</option>
                  <option value="bad">仅坏数据</option>
                </select>
              )}
            </>
          )}
          <button
            type="button"
            className="refresh-data-btn"
            onClick={refreshData}
            title="重新生成数据后，请先在项目根目录执行：cd dashboard && npm run sync:data"
          >
            刷新数据
          </button>
          <div className="segmented">
            <button className={mode === 'vitals' ? 'active' : ''} onClick={() => setMode('vitals')}>
              体征
            </button>
            <button className={mode === 'environment' ? 'active' : ''} onClick={() => setMode('environment')}>
              环境
            </button>
            <button className={mode === 'sleep' ? 'active' : ''} onClick={() => setMode('sleep')}>
              睡眠
            </button>
            <button className={mode === 'ranking' ? 'active' : ''} onClick={() => setMode('ranking')}>
              排名
            </button>
          </div>
        </div>
      </header>
      {mode === 'ranking' ? (
        <RankingView
          personaUid={selectedUser}
          onPersonaChange={setSelectedUser}
          dataRefreshKey={dataRefreshKey}
        />
      ) : (
        <section className="chart-wrap">
          <ReactECharts option={option} notMerge style={{ width: '100%', height: '540px' }} />
        </section>
      )}
    </main>
  )
}

export default App
