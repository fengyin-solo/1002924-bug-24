<template>
  <section class="page" data-module="energyeff">
    <header class="page-head">
      <div>
        <h2>能效监测管理</h2>
        <p class="page-desc">
          统一口径：按设备类型与记录月份合计耗能量 / 合计产量计算单耗指标；
          超过对标基准的记录拦下并提示超出量，口径更新后历史记录按新规则重算。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="openCaliber">对标口径设置</button>
        <button class="btn primary" type="button" @click="openCreate">登记能效记录</button>
        <button class="btn" type="button" @click="exportRows">导出能效监测清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statsCards" :key="item.label" class="stat-card" :class="item.emphasis">
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
        <span>设备类型</span>
        <select v-model="filters.equipment_type">
          <option value="">全部类型</option>
          <option v-for="type in equipmentTypes" :key="type" :value="type">{{ type }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>记录月份</span>
        <input v-model="filters.month" placeholder="YYYY-MM" />
      </label>
      <label class="filter-item">
        <span>能效状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      <label class="filter-check">
        <input v-model="pendingOnly" type="checkbox" @change="reload" />
        只看待分析（已调整不进入）
      </label>
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
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :class="{ 'row-over': row.超标 && row.status !== '已调整', 'row-done': row.status === '已调整' }"
        >
          <td v-for="column in columns" :key="column">{{ display(row, column) }}</td>
          <td class="over-cell">
            <span v-if="row.超标 && row.status !== '已调整'" class="over-flag">⚠ {{ row.超标提示 }}</span>
            <span v-else-if="row.超标" class="muted-text">超标已调整，已退出待分析</span>
            <span v-else class="ok-text">未超标</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="showDetail(row)">详情</button>
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
      <span>共 {{ total }} 条能效监测记录，已按记录月份升序排列</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记弹窗 -->
    <div v-if="createVisible" class="modal-mask" @click.self="createVisible = false">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记能效记录</h3>
        <label v-for="field in createFields" :key="field.key" class="modal-item">
          <span>{{ field.label }}</span>
          <input
            v-model="createForm[field.key]"
            :type="field.type"
            :placeholder="field.placeholder"
            :step="field.type === 'number' ? 'any' : undefined"
          />
        </label>
        <p class="modal-hint">提交时按同设备类型、同月份合计口径试算单耗；超过对标基准将被拦下并说明超出量。</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="createVisible = false">取消</button>
          <button class="btn primary" type="submit">提交登记</button>
        </div>
      </form>
    </div>

    <!-- 口径设置弹窗 -->
    <div v-if="caliberVisible" class="modal-mask" @click.self="caliberVisible = false">
      <form class="modal" @submit.prevent="submitCaliber">
        <h3>对标口径设置</h3>
        <p class="modal-hint">
          单耗单位：{{ caliber?.unit ?? '吨标准煤/吨产品' }}；偏差超过基准
          {{ Math.round((caliber?.significant_threshold ?? 0.1) * 100) }}% 记为显著偏差。
          当前口径版本 v{{ caliber?.version ?? 1 }}（{{ caliber?.updated_at }}）。
        </p>
        <label v-for="type in equipmentTypes" :key="type" class="modal-item">
          <span>{{ type }} 对标基准</span>
          <input v-model.number="caliberForm[type]" type="number" step="any" />
        </label>
        <p class="modal-hint">保存后全部历史记录立即按新规则重算；已调整记录保留判定时的单耗与偏差快照。</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="caliberVisible = false">取消</button>
          <button class="btn primary" type="submit">保存并重算历史数据</button>
        </div>
      </form>
    </div>

    <!-- 详情抽屉：与列表同源，偏差比率一致；已调整记录展示判定快照 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <article class="drawer">
        <header class="drawer-head">
          <h3>{{ detail.记录编号 }} 能效明细</h3>
          <button class="btn ghost" type="button" @click="detail = null">关闭</button>
        </header>
        <dl class="detail-grid">
          <template v-for="item in detailItems" :key="item.label">
            <dt>{{ item.label }}</dt>
            <dd>{{ item.value }}</dd>
          </template>
        </dl>
        <div v-if="detail.超标 && detail.status !== '已调整'" class="callout warn">{{ detail.超标提示 }}</div>
        <div v-if="detail.status === '已调整'" class="callout done">
          该记录已调整完成，不进入待分析清单。
          <template v-if="snapshot">
            <br />判定时（{{ snapshot.判定时间 }}，口径版本 v{{ snapshot.口径版本 }}）：
            单耗 {{ fmt4(snapshot.判定时单耗指标) }}，基准 {{ fmt4(snapshot.判定时对标基准) }}，
            偏差 {{ snapshot.判定时偏差比率显示 }}，结论 {{ snapshot.判定结论 }}。
          </template>
        </div>
        <div v-if="detail.口径错误" class="callout warn">{{ detail.口径错误 }}</div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | CaliberSnapshot>
interface CaliberInfo {
  benchmarks: Record<string, number>
  unit: string
  significant_threshold: number
  version: number
  updated_at: string
  [key: string]: string | number | Record<string, number>
}
interface CaliberSnapshot {
  判定时单耗指标: number | null
  判定时对标基准: number | null
  判定时偏差比率: number | null
  判定时偏差比率显示: string
  判定结论: string
  判定时间: string
  口径版本: number
}

const ENDPOINT = '/api/energyeff'
const columns = ['记录编号', '设备类型', '耗能量', '产量', '单耗指标', '对标基准', '偏差比率', '记录月份', '能效状态']
const statuses = ['达标', '轻微偏差', '显著偏差', '已调整']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const pendingOnly = ref(false)
const filters = ref<Record<string, string>>({ keyword: '', equipment_type: '', month: '', status: '' })

const stats = ref<Record<string, number>>({
  达标设备: 0,
  偏差设备: 0,
  显著偏差设备: 0,
  待分析: 0,
  超标记录: 0,
  已调整: 0,
})
const statsCards = computed(() => [
  { label: '达标设备', value: stats.value.达标设备 ?? 0, emphasis: '' },
  { label: '轻微偏差设备', value: stats.value.偏差设备 ?? 0, emphasis: '' },
  { label: '显著偏差设备', value: stats.value.显著偏差设备 ?? 0, emphasis: 'stat-warn' },
  { label: '待分析', value: stats.value.待分析 ?? 0, emphasis: 'stat-warn' },
  { label: '超标记录', value: stats.value.超标记录 ?? 0, emphasis: 'stat-warn' },
  { label: '已调整', value: stats.value.已调整 ?? 0, emphasis: '' },
])

const caliber = ref<CaliberInfo | null>(null)
const caliberForm = ref<Record<string, number>>({})
const caliberVisible = ref(false)
const equipmentTypes = computed(() => (caliber.value ? Object.keys(caliber.value.benchmarks) : []))

const createVisible = ref(false)
const createFields = [
  { key: '记录编号', label: '记录编号（留空自动生成）', type: 'text', placeholder: '如 ENER-0010' },
  { key: '设备类型', label: '设备类型', type: 'text', placeholder: '锅炉 / 空压机 / 制冷机组 / 风机水泵' },
  { key: '耗能量', label: '耗能量（吨标准煤）', type: 'number', placeholder: '如 132' },
  { key: '产量', label: '产量（吨产品）', type: 'number', placeholder: '如 1200' },
  { key: '记录月份', label: '记录月份（YYYY-MM）', type: 'text', placeholder: '如 2026-07' },
]
const emptyCreateForm = (): Record<string, string> => ({
  记录编号: '',
  设备类型: '',
  耗能量: '',
  产量: '',
  记录月份: '',
})
const createForm = ref<Record<string, string>>(emptyCreateForm())

const detail = ref<Row | null>(null)
const snapshot = computed<CaliberSnapshot | null>(() => {
  const value = detail.value?.判定快照
  return value && typeof value === 'object' ? (value as CaliberSnapshot) : null
})

function fmt4(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(4) : (value as string) ?? '—'
}

function display(row: Row, column: string): string {
  if (column === '单耗指标' || column === '对标基准') return fmt4(row[column])
  if (column === '偏差比率') return String(row.偏差比率显示 ?? '—')
  if (column === '能效状态') return String(row.status ?? row[column] ?? '—')
  if (column === '耗能量' || column === '产量') {
    const value = row[column]
    return typeof value === 'number' ? String(value) : (value as string) ?? '—'
  }
  return row[column] == null ? '—' : String(row[column])
}

function availableActions(row: Row): string[] {
  if (row.status === '已调整') return []
  if (row.status === '达标') return []
  if (row.status === '轻微偏差') return ['记录偏差']
  return ['记录偏差', '分析原因', '调整优化']
}

const detailItems = computed(() => {
  if (!detail.value) return []
  const row = detail.value
  return [
    { label: '设备类型', value: String(row.设备类型 ?? '—') },
    { label: '记录月份', value: String(row.记录月份 ?? '—') },
    { label: '耗能量（吨标准煤）', value: String(row.耗能量 ?? '—') },
    { label: '产量（吨产品）', value: String(row.产量 ?? '—') },
    { label: '单耗指标', value: fmt4(row.单耗指标) },
    { label: '对标基准', value: fmt4(row.对标基准) },
    { label: '偏差比率', value: String(row.偏差比率显示 ?? '—') },
    { label: '超标量', value: row.超标量 == null ? '—' : fmt4(row.超标量) },
    { label: '能效状态', value: String(row.status ?? '—') },
    { label: '是否待分析', value: row.pending ? '是' : '否' },
  ]
})

function resetFilters() {
  filters.value = { keyword: '', equipment_type: '', month: '', status: '' }
  pendingOnly.value = false
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createForm.value = emptyCreateForm()
  errorMessage.value = ''
  createVisible.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '登记未生效，请核对后重试')
    }
    createVisible.value = false
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '能效记录登记失败'
  }
}

async function openCaliber() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/caliber`)
    if (!response.ok) throw new Error('对标口径读取失败')
    caliber.value = (await response.json()) as CaliberInfo
    caliberForm.value = Object.fromEntries(
      Object.entries(caliber.value.benchmarks).map(([key, value]) => [key, value]),
    )
    caliberVisible.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '对标口径读取失败'
  }
}

async function submitCaliber() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/caliber`, {
      method: 'PUT',
      body: JSON.stringify({ values: { benchmarks: caliberForm.value } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '口径未更新，请核对基准数值')
    }
    caliberVisible.value = false
    await Promise.all([reload(), loadStats(), loadCaliberMeta()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '对标口径更新失败'
  }
}

async function showDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('能效记录详情读取失败')
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '能效记录详情读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message ?? '能效监测动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '能效监测操作失败'
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (response.ok) stats.value = await response.json()
  } catch {
    // 统计不影响主列表，静默保留上一次数值
  }
}

async function loadCaliberMeta() {
  try {
    const response = await request(`${ENDPOINT}/caliber`)
    if (response.ok) caliber.value = await response.json()
  } catch {
    // 口径元信息只用于弹窗，加载失败不阻断列表
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  Object.entries(filters.value).forEach(([key, value]) => {
    if (value) query.set(key, value)
  })
  if (pendingOnly.value) query.set('pending', 'true')
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('能效记录列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '能效监测列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadStats()
  void loadCaliberMeta()
})
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.filter-check { display: flex; align-items: center; gap: 4px; font-size: 13px; color: var(--muted); }
.stat-warn .stat-value { color: #b42318; }
.row-over { background: #fef3f2; }
.row-done { color: var(--muted); }
.over-cell { max-width: 360px; }
.over-flag { color: #b42318; font-size: 12px; }
.ok-text { color: #027a48; font-size: 12px; }
.muted-text { color: var(--muted); font-size: 12px; }

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgb(16 24 40 / 45%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 420px;
  max-height: 80vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.modal h3 { margin: 0 0 12px; }
.modal-item { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; font-size: 13px; }
.modal-item input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-hint { font-size: 12px; color: var(--muted); margin: 6px 0 12px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }

.drawer {
  width: 460px;
  max-height: 85vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.drawer-head { display: flex; justify-content: space-between; align-items: center; }
.drawer-head h3 { margin: 0; }
.detail-grid { display: grid; grid-template-columns: 140px 1fr; gap: 6px 12px; font-size: 13px; margin: 12px 0; }
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; }
.callout { border-radius: 8px; padding: 10px 12px; font-size: 13px; line-height: 1.6; }
.callout.warn { background: #fef3f2; color: #b42318; border: 1px solid #fecdca; }
.callout.done { background: #ecfdf3; color: #027a48; border: 1px solid #abefc6; }
</style>
