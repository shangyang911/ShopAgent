import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { fetchCustomerDetail, saveCustomerProfile } from '../api/customerDetailsApi'
import type { CustomerDetail } from '../model/details'

export function useCustomerDetail(customerId: Readonly<Ref<number | null>>) {
  const data = ref<CustomerDetail | null>(null)
  const page = ref(1)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const profileError = ref('')
  const saved = ref(false)
  const tagsDraft = ref('')
  const noteDraft = ref('')
  const revision = ref<number | null>(null)
  const pageCount = computed(() => Math.max(1, Math.ceil((data.value?.orders.total ?? 0) / 10)))
  let generation = 0
  let profileGeneration = 0

  async function load() {
    const target = customerId.value
    const id = ++generation
    if (target == null) return
    loading.value = true
    error.value = ''
    try {
      const result = await fetchCustomerDetail(target, page.value)
      if (id !== generation || target !== customerId.value) return
      data.value = result
      if (page.value > pageCount.value) {
        page.value = pageCount.value
        await load()
        return
      }
      // 翻页和刷新订单不覆盖尚未保存的跟进草稿。
      if (revision.value === null) {
        tagsDraft.value = result.profile.tags.join('，')
        noteDraft.value = result.profile.note
        revision.value = result.profile.revision
      }
    } catch (reason) {
      if (id === generation)
        error.value = reason instanceof Error ? reason.message : '客户详情加载失败'
    } finally {
      if (id === generation) loading.value = false
    }
  }
  async function saveProfile() {
    const target = customerId.value
    if (saving.value || target == null || revision.value == null) return
    const id = ++profileGeneration
    saving.value = true
    profileError.value = ''
    saved.value = false
    try {
      const tags = tagsDraft.value
        .split(/[,，]/)
        .map((value) => value.trim())
        .filter(Boolean)
      const profile = await saveCustomerProfile(target, tags, noteDraft.value, revision.value)
      if (id !== profileGeneration || target !== customerId.value) return
      revision.value = profile.revision
      tagsDraft.value = profile.tags.join('，')
      noteDraft.value = profile.note
      saved.value = true
    } catch (reason) {
      if (id === profileGeneration)
        profileError.value = reason instanceof Error ? reason.message : '跟进资料保存失败'
    } finally {
      if (id === profileGeneration) saving.value = false
    }
  }
  function reloadProfile() {
    // 用户明确放弃草稿后，才能重新取服务端版本解决冲突。
    if (saving.value || !window.confirm('重新加载会放弃未保存的跟进资料，是否继续？')) return
    revision.value = null
    profileError.value = ''
    void load()
  }
  function changePage(value: number) {
    page.value = value
    void load()
  }
  watch(
    customerId,
    () => {
      generation++
      profileGeneration++
      data.value = null
      page.value = 1
      revision.value = null
      tagsDraft.value = ''
      noteDraft.value = ''
      profileError.value = ''
      error.value = ''
      saving.value = false
      loading.value = false
      saved.value = false
      void load()
    },
    { immediate: true },
  )
  onBeforeUnmount(() => {
    generation++
    profileGeneration++
  })
  return {
    data,
    page,
    loading,
    saving,
    error,
    profileError,
    saved,
    tagsDraft,
    noteDraft,
    revision,
    pageCount,
    load,
    saveProfile,
    reloadProfile,
    changePage,
  }
}
