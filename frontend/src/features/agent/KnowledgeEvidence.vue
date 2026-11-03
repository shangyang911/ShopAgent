<script setup lang="ts">
// [知识证据] 展示回答当时保存的原文快照；跳转详情可继续核对版本当前是否适用。
import type { KnowledgeSource } from './types'
defineProps<{ sources: KnowledgeSource[] }>()
</script>
<template>
  <section aria-label="知识引用证据" class="ops-notice">
    <strong>本轮引用依据</strong>
    <details v-for="source in sources" :key="source.id">
      <summary>
        {{ source.title }} · {{ source.version || '版本未提供' }} · {{ source.section || '正文' }}
      </summary>
      <p>
        生效：{{ source.effective_from || '未提供' }} 至
        {{ source.effective_until || '未设置结束日期' }}
      </p>
      <blockquote style="white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0">
        {{ source.excerpt || '历史记录未保留引用原文' }}
      </blockquote>
      <p v-if="source.content_hash" style="overflow-wrap: anywhere">
        内容指纹：{{ source.content_hash }}
      </p>
      <a
        v-if="source.document_id"
        :href="`/knowledge?document_id=${encodeURIComponent(source.document_id)}`"
        >查看版本原文与当前状态</a
      >
      <small> 历史引用为当时证据快照，请核对当前适用版本。</small>
    </details>
  </section>
</template>
