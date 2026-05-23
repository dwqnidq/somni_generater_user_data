import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const dashboardRoot = path.resolve(__dirname, '..')
const repoRoot = path.resolve(dashboardRoot, '..')
const outputDir = path.join(repoRoot, 'output')
const targetDir = path.join(dashboardRoot, 'public', 'data', 'users')
const rankingDir = path.join(dashboardRoot, 'public', 'data', 'ranking')

const FILE_KINDS = ['vitals_data', 'environment_data', 'health_data', 'sleep_events']
const filePattern = /^(?<uid>[0-9a-f]+)_(?<kind>vitals_data|environment_data|health_data|sleep_events)\.json$/

const PERSONA_UIDS = [
  '69aea593af5e6cbf08027964',
  '69aea63eaf5e6cbf08027965',
  '69aea6d8af5e6cbf08027966',
  '69aea6e3af5e6cbf08027967',
  '69aea6e8af5e6cbf08027968',
  '69aea6eeaf5e6cbf08027969',
  '69aea6f3af5e6cbf0802796a',
  '69aea6f8af5e6cbf0802796b',
]

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true })
}

function cleanTargetJsonFiles(dir) {
  if (!fs.existsSync(dir)) return
  for (const name of fs.readdirSync(dir)) {
    if (name.endsWith('.json')) {
      fs.rmSync(path.join(dir, name))
    }
  }
}

function syncFiles() {
  if (!fs.existsSync(outputDir)) {
    throw new Error(`output 目录不存在: ${outputDir}`)
  }

  ensureDir(targetDir)
  cleanTargetJsonFiles(targetDir)

  const userKinds = new Map()
  let copied = 0

  for (const name of fs.readdirSync(outputDir)) {
    const match = name.match(filePattern)
    if (!match?.groups) continue

    const { uid, kind } = match.groups
    if (!FILE_KINDS.includes(kind)) continue

    fs.copyFileSync(path.join(outputDir, name), path.join(targetDir, name))
    copied += 1

    if (!userKinds.has(uid)) userKinds.set(uid, new Set())
    userKinds.get(uid).add(kind)
  }

  const manifest = []
  for (const [uid, kinds] of userKinds.entries()) {
    const ok = FILE_KINDS.every((kind) => kinds.has(kind))
    if (ok) manifest.push({ uid })
  }
  manifest.sort((a, b) => a.uid.localeCompare(b.uid))

  fs.writeFileSync(path.join(targetDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')

  console.log(`Copied ${copied} files.`)
  console.log(`Manifest users: ${manifest.length}.`)
}

function syncRankingData() {
  ensureDir(rankingDir)
  cleanTargetJsonFiles(rankingDir)

  const mainFile = path.join(outputDir, 'somni_sleep_analysis.json')
  if (fs.existsSync(mainFile)) {
    fs.copyFileSync(mainFile, path.join(rankingDir, 'somni_sleep_analysis.json'))
    const sizeMB = (fs.statSync(mainFile).size / 1024 / 1024).toFixed(1)
    console.log(`Ranking: copied somni_sleep_analysis.json (${sizeMB} MB)`)
  } else {
    console.warn('Ranking: somni_sleep_analysis.json not found')
  }

  let personaCount = 0
  for (const uid of PERSONA_UIDS) {
    const src = path.join(outputDir, `${uid}_somni_sleep_analysis.json`)
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(rankingDir, `${uid}_somni_sleep_analysis.json`))
      personaCount++
    }
  }
  console.log(`Ranking: copied ${personaCount} persona user files`)
}

syncFiles()
syncRankingData()
