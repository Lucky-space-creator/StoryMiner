<template>
  <div class="overflow-x-auto border border-app rounded-[var(--radius-md)]">
    <table class="w-full text-sm">
      <thead class="bg-surface2 text-muted">
        <tr>
          <th v-for="c in columns" :key="c.key" class="text-left font-medium px-4 py-3 whitespace-nowrap">{{ c.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, i) in rows"
          :key="row.id ?? i"
          class="border-t border-app hover:bg-surface2 transition"
        >
          <td v-for="c in columns" :key="c.key" class="px-4 py-3 text-app align-middle">
            <slot :name="'cell-' + c.key" :row="row" :value="row[c.key]">{{ row[c.key] }}</slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] }
})
</script>
