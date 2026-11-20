<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
const overview = ref(null)
const bay = ref([])
const drill = ref(null)
const drillErr = ref('')
const verify = ref(null)

async function load() {
  overview.value = await api.overview()
  bay.value = await api.bay()
  verify.value = await api.conflictIndexAll()
}
async function openCell(cell) {
  drillErr.value = ''
  if (!cell.conflict?.count) { drill.value = null; return }
  try {
    drill.value = await api.conflictIndex(cell.id)
  } catch (e) {
    drillErr.value = String(e)
  }
}
onMounted(load)
</script>
<template>
  <div data-plant="18-slot-conflict-index-miss" class="plant-mark" style="display:none"></div>

  <div>
    <h1>格阵</h1>
    <p class="sub">柜机格口占用态一眼可核；空闲 / 占用由装载事件物化。冲突角标与反查同源。</p>
    <div class="stats" v-if="overview">
      <div class="stat"><b>{{ overview.cells }}</b><span>格口</span></div>
      <div class="stat"><b>{{ overview.occupied }}</b><span>占用</span></div>
      <div class="stat"><b>{{ overview.free }}</b><span>空闲</span></div>
      <div class="stat"><b>{{ overview.slot_conflicts }}</b><span>冲突组</span></div>
      <div class="stat"><b>{{ overview.open_occupancies }}</b><span>开放占用账</span></div>
    </div>
    <p v-if="verify && !verify.ok" class="warn" data-role="index-inconsistent">
      冲突反查索引不一致：{{ verify.issues.length }} 处问题（缺行 / 缺后到事件 / 串格）。
      <router-link to="/proofs">去核账</router-link>
    </p>
    <section v-for="cab in bay" :key="cab.id" class="cabinet panel">
      <h2>{{ cab.label }} <small>{{ cab.id }} · {{ cab.site }}</small></h2>
      <div class="grid">
        <div v-for="cell in cab.cells" :key="cell.id" class="cell" :class="cell.status">
          <div>
            <div class="code">{{ cell.code }}</div>
            <div class="meta">{{ cell.size_code }} · {{ cell.status === 'occupied' ? '占用' : '空闲' }}</div>
          </div>
          <div class="meta" v-if="cell.status === 'occupied' && cell.occupancy?.tracking_no">{{ cell.occupancy.tracking_no }}</div>
          <button
            v-if="cell.conflict?.count"
            class="badge"
            :class="{ bad: cell.conflict.ok === false }"
            data-role="conflict-badge"
            @click="openCell(cell)"
          >冲突 {{ cell.conflict.count }}</button>
          <span class="dot" />
        </div>
      </div>
    </section>
    <p v-if="drillErr" class="warn">{{ drillErr }}</p>
    <div v-if="drill" class="panel" style="margin-top:1rem; overflow:auto">
      <h2 style="margin:0 0 .5rem;font-size:1rem">
        格口 {{ drill.cell_id }} 冲突组 · 反查 {{ drill.indexed ? '已接通' : '空' }}
      </h2>
      <p v-if="!drill.ok" class="warn">反查核证未通过：{{ drill.issues.map(i => i.code).join('、') }}</p>
      <table>
        <thead><tr><th>冲突</th><th>先占占用</th><th>先到事件</th><th>后到 drop</th><th>搁置键</th><th>时刻</th></tr></thead>
        <tbody>
          <tr v-for="h in drill.hits" :key="h.id">
            <td>{{ h.conflict_id }}</td>
            <td><code>{{ h.occupancy_id }}</code></td>
            <td><code>{{ h.open_event_id }}</code></td>
            <td><code>{{ h.drop_event_id }}</code></td>
            <td><code>{{ h.hold_key }}</code></td>
            <td>{{ h.created_at }}</td>
          </tr>
          <tr v-if="!drill.hits.length"><td colspan="6" class="sub">该格口无反查索引行。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
