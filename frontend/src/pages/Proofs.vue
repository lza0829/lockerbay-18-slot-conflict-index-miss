<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
const proofs = ref([])
const conflicts = ref([])
const detail = ref(null)
const indexOf = ref(null)
const verify = ref(null)
async function load() {
  proofs.value = await api.proofs()
  conflicts.value = await api.slotConflicts()
  verify.value = await api.conflictVerify()
}
async function open(id) {
  detail.value = await api.slotConflict(id)
  indexOf.value = await api.slotConflictIndex(id)
}
onMounted(load)
</script>
<template>
  <div data-plant="18-slot-conflict-index-miss" class="plant-mark" style="display:none"></div>

  <div>
    <h1>核账</h1>
    <p class="sub">占用核对与槽位冲突核对（只读）；同格二次投柜整组搁置，双方载荷可回看，正查与格口反查互逆。</p>
    <div class="panel" style="overflow:auto">
      <h2 style="margin:0 0 .5rem;font-size:1rem">占用核对</h2>
      <table>
        <thead><tr><th>占用</th><th>格口</th><th>运单</th><th>开启</th><th>事件</th></tr></thead>
        <tbody>
          <tr v-for="p in proofs" :key="p.id">
            <td><code>{{ p.occupancy_id }}</code></td>
            <td>{{ p.cell_id }}</td>
            <td>{{ p.tracking_no }}</td>
            <td>{{ p.opened_at }}</td>
            <td><code>{{ p.event_ids_json }}</code></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="verify" class="panel" style="margin-top:1rem" data-role="index-verdict">
      <h2 style="margin:0 0 .5rem;font-size:1rem">冲突反查核证</h2>
      <p v-if="verify.ok" class="ok-text">
        通过 · 冲突组 {{ verify.checked.conflicts }} / 反查行 {{ verify.checked.index_rows }} / 搁置 {{ verify.checked.holds }}
      </p>
      <template v-else>
        <p class="warn">未通过 · {{ verify.issues.length }} 处</p>
        <table>
          <thead><tr><th>问题</th><th>格口</th><th>冲突</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="(i, n) in verify.issues" :key="n">
              <td><code>{{ i.code }}</code></td>
              <td>{{ i.cell_id || '—' }}</td>
              <td>{{ i.conflict_id ?? '—' }}</td>
              <td>{{ i.detail }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>
    <div class="panel" style="margin-top:1rem; overflow:auto">
      <h2 style="margin:0 0 .5rem;font-size:1rem">槽位冲突核对</h2>
      <p v-if="!conflicts.length" class="sub">暂无槽位冲突；同格二次投柜会在装载入口整组搁置并在此落证。</p>
      <table v-else>
        <thead><tr><th>#</th><th>格口</th><th>先占占用</th><th>先占事件</th><th>冲突 drop</th><th>反查</th><th>时刻</th><th></th></tr></thead>
        <tbody>
          <tr v-for="c in conflicts" :key="c.id">
            <td>{{ c.id }}</td>
            <td>{{ c.cell_id }}</td>
            <td><code>{{ c.occupancy_id }}</code></td>
            <td><code>{{ c.open_event_id }}</code></td>
            <td><code>{{ c.drop_event_id }}</code></td>
            <td>
              <span v-if="c.indexed" class="ok-text"><code>{{ c.hold_key }}</code> 已接通</span>
              <span v-else class="warn">缺反查</span>
            </td>
            <td>{{ c.created_at }}</td>
            <td><button @click="open(c.id)">回看载荷</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="detail" class="panel" style="margin-top:.75rem">
        <h2 style="margin:0 0 .5rem;font-size:1rem">冲突 #{{ detail.id }} 整组载荷 · {{ detail.cell_id }}</h2>
        <p v-if="indexOf" class="sub">
          正查反打（冲突 → 格口反查）：
          <span v-if="indexOf.ok" class="ok-text">互逆一致 · 索引行 {{ indexOf.index?.id }}</span>
          <span v-else class="warn">不一致 · {{ indexOf.issues.map(i => i.code).join('、') }}</span>
        </p>
        <table>
          <thead><tr><th>角色</th><th>事件</th><th>类型</th><th>运单</th><th>时刻</th><th>载荷</th></tr></thead>
          <tbody>
            <tr v-for="e in detail.member.events" :key="e.role">
              <td>{{ e.role === 'open' ? '先占' : '冲突 drop' }}</td>
              <td><code>{{ e.id }}</code></td>
              <td>{{ e.event_type || '—' }}</td>
              <td>{{ e.tracking_no || '—' }}</td>
              <td>{{ e.event_ts || '—' }}</td>
              <td><code>{{ JSON.stringify(e.payload) }}</code></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
