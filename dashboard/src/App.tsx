import { useEffect, useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'

type Mode = 'vitals' | 'environment' | 'sleep'

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

  useEffect(() => {
    fetchJsonNoStore<UserManifestItem[]>('/data/users/manifest.json')
      .then((list: UserManifestItem[]) => {
        setUsers(list)
        setSelectedUser(list[0]?.uid ?? '')
      })
  }, [])

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
  }, [selectedUser])

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
            const p1 = api.coord([x, fromY]) // 上一阶段中心点
            const p2 = api.coord([x, toY]) // 下一阶段中心点
            // 直接使用中心点到中心点，保证连接与阶段块连续，不出现脱离感
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
          <select value={selectedUser} onChange={(e) => setSelectedUser(e.target.value)}>
            {users.map((u) => (
              <option key={u.uid} value={u.uid}>
                用户 {u.uid}
              </option>
            ))}
          </select>
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
          </div>
        </div>
      </header>
      <section className="chart-wrap">
        <ReactECharts option={option} notMerge style={{ width: '100%', height: '540px' }} />
      </section>
    </main>
  )
}

export default App
