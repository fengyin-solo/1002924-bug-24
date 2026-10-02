<template>
  <section class="page" data-module="energyeff">
    <header class="page-head">
      <div>
        <h2>能效监测管理</h2>
        <p class="page-desc">单耗指标按设备类型与记录月份统一对标：单耗指标 = 耗能量 ÷ 对标产量，偏差比率 =（单耗指标 − 对标基准）÷ 对标基准。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记能效记录</button>
        <button class="btn" type="button" @click="openBenchmarks">维护对标口径</button>
        <button class="btn" type="button" @click="exportRows">导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card" :class="item.danger ? 'stat-danger' : ''">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号检索" />
      </label>
      <label class="filter-item">
        <span>能效状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-check">
        <input v-model="filters.pending" type="checkbox" :true-value="'true'" :false-value="''" />
        <span>只看待分析清单</span>
      </label>
      <label class="filter-check">
        <input v-model="filters.overLimit" type="checkbox" :true-value="'true'" :false-value="''" />
        <span>只看超标记录</span>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>超标提示</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-over': row.overLimit && row.能效状态 !== '已调整' }">
          <td>{{ row['记录编号'] ?? '—' }}</td>
          <td>{{ row['设备类型'] ?? '—' }}</td>
          <td>{{ row['耗能量'] ?? '—' }}</td>
          <td>{{ formatValue(row['单耗指标']) }}</td>
          <td>{{ formatValue(row['对标基准']) }}</td>
          <td :class="deviationClass(row)">{{ formatDeviation(row['偏差比率']) }}</td>
          <td>{{ row['记录月份'] ?? '—' }}</td>
          <td>{{ row['能效状态'] ?? '—' }}</td>
          <td>
            <span v-if="!row.benchmarkable" class="warn-text">{{ row.benchmarkMessage || '口径缺失' }}</span>
            <span v-else-if="row.overLimit" class="error-text">
              超 {{ row.overLimitAmount }}（{{ formatDeviation(row['偏差比率']) }}）
            </span>
            <span v-else class="ok-text">未超标</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无能效监测数据，可先登记能效记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条能效记录（按记录月份升序）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记弹窗 -->
    <div v-if="createVisible" class="modal-mask" @click.self="closeCreate">
      <div class="modal">
        <h3>登记能效记录</h3>
        <p class="modal-hint">提交时按设备类型与记录月份现算单耗指标；超过对标基准会被拦下并说明超标量。</p>
        <label v-for="field in createFields" :key="field.key" class="form-item">
          <span>{{ field.label }}</span>
          <input v-model="createForm[field.key]" :placeholder="field.placeholder" />
        </label>
        <p v-if="createMessage" class="error-text">{{ createMessage }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeCreate">取消</button>
          <button v-if="createOverLimit" class="btn warn" type="button" @click="submitCreate(true)">强制登记并进入待分析</button>
          <button class="btn primary" type="button" @click="submitCreate(false)">提交</button>
        </div>
      </div>
    </div>

    <!-- 调整优化弹窗 -->
    <div v-if="adjustVisible" class="modal-mask" @click.self="adjustVisible = false">
      <div class="modal">
        <h3>调整优化 · {{ adjustTarget?.['记录编号'] }}</h3>
        <p class="modal-hint">调整后该记录退出待分析清单，并保留调整时的判定偏差作为历史快照。</p>
        <label class="form-item">
          <span>调整后耗能量（可选，留空则仅关闭偏差）</span>
          <input v-model="adjustedEnergy" placeholder="如 980" />
        </label>
        <p v-if="adjustMessage" class="error-text">{{ adjustMessage }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="adjustVisible = false">取消</button>
          <button class="btn primary" type="button" @click="confirmAdjust">确认调整</button>
        </div>
      </div>
    </div>

    <!-- 详情弹窗：与列表同一口径，同时展示历史快照与当前口径 -->
    <div v-if="detailVisible" class="modal-mask" @click.self="detailVisible = false">
      <div class="modal">
        <h3>能效记录详情 · {{ detail?.['记录编号'] }}</h3>
        <table class="detail-table">
          <tbody>
            <tr v-for="item in detailRows" :key="item.label">              <th>{{ item.label }}</th>
              <td>{{ item.value }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detailVisible = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 对标口径维护 -->
    <div v-if="benchmarkVisible" class="modal-mask modal-wide" @click.self="benchmarkVisible = false">
      <div class="modal">
        <h3>对标口径维护</h3>
        <p class="modal-hint">保存后历史记录会立即按新口径重算；已调整记录的历史偏差快照保持不变。</p>
        <table class="data-table">
          <thead>
            <tr><th>设备类型</th><th>记录月份</th><th>对标基准（单耗）</th><th>对标产量（分母）</th></tr>
          </thead>
          <tbody>
            <tr v-for="b in benchmarks" :key="String(b.id)">
              <td>{{ b['设备类型'] }}</td>
              <td>{{ b['记录月份'] }}</td>
              <td>{{ b['对标基准'] }}</td>
              <td>{{ b['对标产量'] }}</td>
            </tr>
            <tr v-if="!benchmarks.length">
              <td colspan="4" class="empty-state">暂无口径，请在下方新增</td>
            </tr>
          </tbody>
        </table>
        <div class="form-grid">
          <label class="form-item">
            <span>设备类型</span>
            <input v-model="benchmarkForm.设备类型" placeholder="如 燃气锅炉" />
          </label>
          <label class="form-item">
            <span>记录月份</span>
            <input v-model="benchmarkForm.记录月份" placeholder="YYYY-MM，如 2026-09" />
          </label>
          <label class="form-item">
            <span>对标基准</span>
            <input v-model="benchmarkForm.对标基准" placeholder="如 1.0" />
          </label>
          <label class="form-item">
            <span>对标产量</span>
            <input v-model="benchmarkForm.对标产量" placeholder="如 1000" />
          </label>
        </div>
        <p v-if="benchmarkMessage" :class="benchmarkOk ? 'ok-text' : 'error-text'">{{ benchmarkMessage }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="benchmarkVisible = false">关闭</button>
          <button class="btn primary" type="button" @click="saveBenchmark">保存口径并重算</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/energyeff'
const columns = ['记录编号', '设备类型', '耗能量', '单耗指标', '对标基准', '偏差比率', '记录月份', '能效状态']
const statuses = ['达标', '轻微偏差', '显著偏差', '已调整']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', status: '', pending: '', overLimit: '' })
const allRows = ref<Row[]>([])

const stats = computed(() => [
  { label: '达标记录', value: allRows.value.filter((r) => r['能效状态'] === '达标').length, danger: false },
  { label: '待分析（超标未调整）', value: allRows.value.filter((r) => r.pending === true).length, danger: true },
  { label: '显著偏差', value: allRows.value.filter((r) => r['能效状态'] === '显著偏差').length, danger: true },
  { label: '已调整', value: allRows.value.filter((r) => r['能效状态'] === '已调整').length, danger: false },
])

function resetFilters() {
  filters.value = { keyword: '', status: '', pending: '', overLimit: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function availableActions(row: Row): string[] {
  if (row['能效状态'] === '已调整') return []
  return ['记录偏差', '分析原因', '调整优化']
}

function formatValue(value: unknown): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function formatDeviation(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return `${num > 0 ? '+' : ''}${num}%`
}

function deviationClass(row: Row): string {
  const value = row['偏差比率']
  if (value === null || value === undefined || value === '') return ''
  return Number(value) > 5 ? 'dev-high' : Number(value) > 0 ? 'dev-mid' : 'dev-ok'
}

// ---- 登记 ----

const createVisible = ref(false)
const createMessage = ref('')
const createOverLimit = ref(false)
const createFields = [
  { key: '记录编号', label: '记录编号', placeholder: '如 ENER-0100' },
  { key: '设备类型', label: '设备类型', placeholder: '如 燃气锅炉' },
  { key: '耗能量', label: '耗能量', placeholder: '本月耗能总量（数字）' },
  { key: '记录月份', label: '记录月份', placeholder: 'YYYY-MM，如 2026-09' },
]
const emptyCreate = () => ({ 记录编号: '', 设备类型: '', 耗能量: '', 记录月份: '' })
const createForm = ref<Record<string, string>>(emptyCreate())

function openCreate() {
  createForm.value = emptyCreate()
  createMessage.value = ''
  createOverLimit.value = false
  createVisible.value = true
}

function closeCreate() {
  createVisible.value = false
}

async function submitCreate(force: boolean) {
  createMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm.value, force } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      createMessage.value = payload?.message ?? '登记失败'
      createOverLimit.value = payload?.message?.includes('超过对标基准') ?? false
      return
    }
    createVisible.value = false
    await reload()
  } catch (error) {
    createMessage.value = error instanceof Error ? error.message : '登记请求失败'
  }
}

// ---- 动作流转 ----

async function runAction(action: string, row: Row) {
  if (action === '调整优化') {
    openAdjust(row)
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload?.message ?? '动作未生效')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '能效监测操作失败'
  }
}

const adjustVisible = ref(false)
const adjustMessage = ref('')
const adjustTarget = ref<Row | null>(null)
const adjustedEnergy = ref('')

function openAdjust(row: Row) {
  adjustTarget.value = row
  adjustedEnergy.value = ''
  adjustMessage.value = ''
  adjustVisible.value = true
}

async function confirmAdjust() {
  if (!adjustTarget.value) return
  adjustMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${adjustTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '调整优化', 调整后耗能量: adjustedEnergy.value } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      adjustMessage.value = payload?.message ?? '调整未生效'
      return
    }
    adjustVisible.value = false
    await reload()
  } catch (error) {
    adjustMessage.value = error instanceof Error ? error.message : '调整请求失败'
  }
}

// ---- 详情 ----

const detailVisible = ref(false)
const detail = ref<Record<string, unknown> | null>(null)

const detailRows = computed(() => {
  if (!detail.value) return []
  const d = detail.value
  const rowsToShow: Array<[string, unknown]> = [
    ['记录编号', d['记录编号']],
    ['设备类型', d['设备类型']],
    ['耗能量', d['耗能量']],
    ['记录月份', d['记录月份']],
    ['单耗指标（统一口径）', formatValue(d['单耗指标'] as string)],
    ['对标基准', formatValue(d['对标基准'] as string)],
    ['偏差比率（判定时）', formatDeviation(d['偏差比率'])],
    ['当前口径重算偏差', formatDeviation(d['当前偏差比率'])],
    ['当前口径重算单耗', formatValue(d['当前单耗指标'] as string)],
    ['超标量', d['overLimitAmount'] ?? '—'],
    ['能效状态', d['能效状态']],
    ['待分析', d['pending'] ? '是' : '否'],
    ['口径说明', (d['benchmarkMessage'] as string) || '口径完整'],
  ]
  return rowsToShow.map(([label, value]) => ({ label, value: String(value ?? '—') }))
})

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('详情读取失败')
    detail.value = await response.json()
    detailVisible.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '详情读取失败'
  }
}

// ---- 对标口径 ----

const benchmarkVisible = ref(false)
const benchmarkMessage = ref('')
const benchmarkOk = ref(false)
const benchmarks = ref<Row[]>([])
const emptyBenchmark = () => ({ 设备类型: '', 记录月份: '', 对标基准: '', 对标产量: '' })
const benchmarkForm = ref<Record<string, string>>(emptyBenchmark())

async function openBenchmarks() {
  benchmarkVisible.value = true
  benchmarkMessage.value = ''
  await loadBenchmarks()
}

async function loadBenchmarks() {
  const response = await request(`${ENDPOINT}/benchmarks/all`)
  if (response.ok) {
    const payload = await response.json()
    benchmarks.value = payload.items ?? []
  }
}

async function saveBenchmark() {
  benchmarkMessage.value = ''
  benchmarkOk.value = false
  const response = await request(`${ENDPOINT}/benchmarks`, {
    method: 'POST',
    body: JSON.stringify({ values: benchmarkForm.value }),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload?.ok) {
    benchmarkMessage.value = payload?.message ?? '口径保存失败'
    return
  }
  benchmarkMessage.value = payload.message
  benchmarkOk.value = true
  benchmarkForm.value = emptyBenchmark()
  await loadBenchmarks()
  await reload()
}

// ---- 列表加载 ----

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  Object.entries(filters.value).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('能效记录列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '能效监测列表读取失败'
  }
  // 统计卡片始终基于全量口径结果
  try {
    const fullResponse = await request(`${ENDPOINT}/export`)
    if (fullResponse.ok) {
      const fullPayload = await fullResponse.json()
      allRows.value = fullPayload.items ?? []
    }
  } catch {
    // 统计失败不阻塞列表
  }
}

onMounted(reload)
</script>
