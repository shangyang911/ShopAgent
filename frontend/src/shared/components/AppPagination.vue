<script setup lang="ts">
// 通用分页：展示当前结果范围并向页面发出翻页请求。
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { useI18n } from '../i18n'

defineProps<{
  page: number
  pageSize: number
  pageCount: number
  total: number
  loading: boolean
}>()

const emit = defineEmits<{ change: [page: number] }>()
const { t } = useI18n()
</script>

<template>
  <footer v-if="!loading && total" class="pagination">
    <span
      >{{ t('Showing') }} {{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, total) }} /
      {{ total }}</span
    >
    <div>
      <button
        :disabled="page <= 1"
        :aria-label="t('Previous page')"
        @click="emit('change', page - 1)"
      >
        <ChevronLeft :size="16" />
      </button>
      <button
        v-for="pageNumber in pageCount"
        :key="pageNumber"
        :class="{ current: page === pageNumber }"
        @click="emit('change', pageNumber)"
      >
        {{ pageNumber }}
      </button>
      <button
        :disabled="page >= pageCount"
        :aria-label="t('Next page')"
        @click="emit('change', page + 1)"
      >
        <ChevronRight :size="16" />
      </button>
    </div>
  </footer>
</template>
