<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
const proofs = ref([])
const conflicts = ref([])
const detail = ref(null)
async function open(id) { detail.value = await api.slotConflict(id) }
onMounted(async () => {
  proofs.value = await api.proofs()
  conflicts.value = await api.slotConflicts()
})
</script>
<template>
  <div data-plant="18-slot-conflict-index-miss" class="plant-mark" style="display:none"></div>

  <div>
    <h1>核账</h1>
    <p class="sub">占用核对与槽位冲突核对（只读）；同格二次投柜整组搁置，双方载荷可回看。</p>
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
    <div class="panel" style="margin-top:1rem; overflow:auto">
      <h2 style="margin:0 0 .5rem;font-size:1rem">槽位冲突核对</h2>
      <p v-if="!conflicts.length" class="sub">暂无槽位冲突；同格二次投柜会在装载入口整组搁置并在此落证。</p>
      <table v-else>
        <thead><tr><th>#</th><th>格口</th><th>先占占用</th><th>先占事件</th><th>冲突 drop</th><th>时刻</th><th></th></tr></thead>
        <tbody>
          <tr v-for="c in conflicts" :key="c.id">
            <td>{{ c.id }}</td>
            <td>{{ c.cell_id }}</td>
            <td><code>{{ c.occupancy_id }}</code></td>
            <td><code>{{ c.open_event_id }}</code></td>
            <td><code>{{ c.drop_event_id }}</code></td>
            <td>{{ c.created_at }}</td>
            <td><button @click="open(c.id)">回看载荷</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="detail" class="panel" style="margin-top:.75rem">
        <h2 style="margin:0 0 .5rem;font-size:1rem">冲突 #{{ detail.id }} 整组载荷 · {{ detail.cell_id }}</h2>
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
