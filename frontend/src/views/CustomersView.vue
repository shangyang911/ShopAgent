<script setup lang="ts">
// [客户页面] 管理客户基本资料和可复用的收货地址。
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ChevronRight, LogOut, Plus, Users, X } from '@lucide/vue'
import LanguageSwitcher from '../shared/components/LanguageSwitcher.vue'
import AppSidebar from '../shared/components/AppSidebar.vue'
import CustomerDetailPanel from '../features/customers/components/CustomerDetailPanel.vue'
import {
  createCustomer,
  createCustomerAddress,
  deleteCustomerAddress,
  fetchCustomerAddresses,
  fetchCustomers,
  updateCustomer,
  updateCustomerAddress,
} from '../features/customers/api/customersApi'
import type { AddressRecord, CustomerRecord } from '../features/customers/model/types'
import { logout } from '../features/auth/api/authApi'
import { useI18n } from '../shared/i18n'

const props = defineProps<{ username: string }>()
const { t } = useI18n()
const customers = ref<CustomerRecord[]>([])
const addresses = ref<AddressRecord[]>([])
const selectedId = ref<number | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const formError = ref('')
const formMode = ref<
  'customer-create' | 'customer-edit' | 'address-create' | 'address-edit' | null
>(null)
const editingAddressId = ref<number | null>(null)
let addressRequestId = 0
let customerRequestId = 0

const customerForm = reactive({ name: '', phone: '', email: '' })
const addressForm = reactive({
  recipient_name: '',
  phone: '',
  country: '中国',
  province: '',
  city: '',
  district: '',
  detail: '',
  postal_code: '',
  longitude: '',
  latitude: '',
  is_default: false,
})

const selectedCustomer = () =>
  customers.value.find((customer) => customer.id === selectedId.value) ?? null

async function loadCustomers(preferredId?: number) {
  const requestId = ++customerRequestId
  loading.value = true
  error.value = ''
  try {
    const result = await fetchCustomers()
    if (requestId !== customerRequestId) return
    customers.value = result
    if (preferredId && customers.value.some((customer) => customer.id === preferredId))
      selectedId.value = preferredId
    else if (selectedId.value == null && customers.value.length)
      selectedId.value = customers.value[0].id
    else if (
      selectedId.value != null &&
      !customers.value.some((customer) => customer.id === selectedId.value)
    ) {
      selectedId.value = customers.value[0]?.id ?? null
    }
  } catch (reason) {
    if (requestId === customerRequestId)
      error.value = reason instanceof Error ? reason.message : t('Could not load customers')
  } finally {
    if (requestId === customerRequestId) loading.value = false
  }
}

async function loadAddresses(customerId: number | null) {
  const requestId = ++addressRequestId
  addresses.value = []
  if (customerId == null) return
  try {
    const rows = await fetchCustomerAddresses(customerId)
    if (requestId !== addressRequestId || selectedId.value !== customerId) return
    addresses.value = rows
  } catch (reason) {
    if (requestId !== addressRequestId || selectedId.value !== customerId) return
    error.value = reason instanceof Error ? reason.message : t('Could not load addresses')
  }
}

watch(selectedId, loadAddresses)
onMounted(() => loadCustomers())
onBeforeUnmount(() => {
  addressRequestId++
  customerRequestId++
})

function resetCustomerForm(customer?: CustomerRecord) {
  customerForm.name = customer?.name ?? ''
  customerForm.phone = customer?.phone ?? ''
  customerForm.email = customer?.email ?? ''
}

function resetAddressForm(address?: AddressRecord) {
  addressForm.recipient_name = address?.recipient_name ?? ''
  addressForm.phone = address?.phone ?? ''
  addressForm.country = address?.country ?? '中国'
  addressForm.province = address?.province ?? ''
  addressForm.city = address?.city ?? ''
  addressForm.district = address?.district ?? ''
  addressForm.detail = address?.detail ?? ''
  addressForm.postal_code = address?.postal_code ?? ''
  addressForm.longitude = address?.longitude == null ? '' : String(address.longitude)
  addressForm.latitude = address?.latitude == null ? '' : String(address.latitude)
  addressForm.is_default = address?.is_default ?? addresses.value.length === 0
}

function openCustomerForm(editing: boolean) {
  formError.value = ''
  resetCustomerForm(editing ? (selectedCustomer() ?? undefined) : undefined)
  formMode.value = editing ? 'customer-edit' : 'customer-create'
}

function openAddressForm(address?: AddressRecord) {
  formError.value = ''
  editingAddressId.value = address?.id ?? null
  resetAddressForm(address)
  formMode.value = address ? 'address-edit' : 'address-create'
}

function closeForm() {
  if (saving.value) return
  formMode.value = null
  formError.value = ''
}

function optionalCoordinate(value: string): number | null {
  if (!value.trim()) return null
  const number = Number(value)
  if (!Number.isFinite(number)) throw new Error(t('Coordinates must be numbers'))
  return number
}

async function saveCustomer() {
  saving.value = true
  formError.value = ''
  try {
    const values = {
      name: customerForm.name.trim(),
      phone: customerForm.phone.trim(),
      email: customerForm.email.trim() || null,
    }
    if (formMode.value === 'customer-edit' && selectedId.value != null) {
      await updateCustomer(selectedId.value, values)
      await loadCustomers(selectedId.value)
    } else {
      const created = await createCustomer(values)
      selectedId.value = created.id
      await loadCustomers(created.id)
    }
    formMode.value = null
  } catch (reason) {
    formError.value = reason instanceof Error ? reason.message : t('Could not save customer')
  } finally {
    saving.value = false
  }
}

async function saveAddress() {
  if (selectedId.value == null) return
  saving.value = true
  formError.value = ''
  try {
    const values = {
      recipient_name: addressForm.recipient_name.trim(),
      phone: addressForm.phone.trim(),
      country: addressForm.country.trim(),
      province: addressForm.province.trim(),
      city: addressForm.city.trim(),
      district: addressForm.district.trim(),
      detail: addressForm.detail.trim(),
      postal_code: addressForm.postal_code.trim() || null,
      longitude: optionalCoordinate(addressForm.longitude),
      latitude: optionalCoordinate(addressForm.latitude),
      is_default: addressForm.is_default,
    }
    if (formMode.value === 'address-edit' && editingAddressId.value != null) {
      await updateCustomerAddress(editingAddressId.value, values)
    } else {
      await createCustomerAddress({ ...values, customer_id: selectedId.value })
    }
    await loadAddresses(selectedId.value)
    formMode.value = null
  } catch (reason) {
    formError.value = reason instanceof Error ? reason.message : t('Could not save address')
  } finally {
    saving.value = false
  }
}

async function removeAddress(address: AddressRecord) {
  if (!window.confirm(t('Delete address confirmation'))) return
  error.value = ''
  try {
    await deleteCustomerAddress(address.id)
    await loadAddresses(selectedId.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not delete address')
  }
}

async function signOut() {
  try {
    await logout()
    window.location.assign('/login')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Sign out failed')
  }
}
</script>

<template>
  <main class="app-frame">
    <AppSidebar active-page="customers" />
    <section class="workspace">
      <header class="topbar">
        <div class="breadcrumb">
          <span>{{ t('Corelystics Store') }}</span
          ><ChevronRight :size="15" /><strong><Users :size="15" /> {{ t('Customers') }}</strong>
        </div>
        <div class="shipping-topbar-spacer"></div>
        <div class="top-actions">
          <LanguageSwitcher /><span class="signed-in-user" :title="props.username">{{
            props.username
          }}</span
          ><button class="icon-button" :aria-label="t('Sign out')" @click="signOut">
            <LogOut :size="18" />
          </button>
        </div>
      </header>
      <section class="shipping-page-content">
        <div class="shipping-heading">
          <div>
            <p class="shipping-eyebrow">{{ t('CUSTOMER MANAGEMENT') }}</p>
            <h1>{{ t('Customers') }}</h1>
            <p>{{ t('Customer records and saved delivery addresses.') }}</p>
          </div>
          <div class="section-actions">
            <button
              class="secondary-action"
              :disabled="!selectedCustomer()"
              @click="openCustomerForm(true)"
            >
              {{ t('Edit customer') }}
            </button>
            <button class="add-product-button" @click="openCustomerForm(false)">
              <Plus :size="15" /> {{ t('Add customer') }}
            </button>
          </div>
        </div>
        <p v-if="loading" class="module-empty">{{ t('Loading customers…') }}</p>
        <p v-else-if="error" class="auth-error">
          {{ error }} <button class="text-action" @click="loadCustomers()">{{ t('Retry') }}</button>
        </p>
        <p v-else-if="customers.length === 0" class="module-empty">{{ t('No customers found') }}</p>
        <div v-else class="customer-layout">
          <nav class="customer-list" :aria-label="t('Customers')">
            <button
              v-for="customer in customers"
              :key="customer.id"
              :class="{ selected: selectedId === customer.id }"
              @click="selectedId = customer.id"
            >
              <strong>{{ customer.name }}</strong
              ><small>{{ customer.phone }}</small>
            </button>
          </nav>
          <div class="address-list">
            <CustomerDetailPanel :customer-id="selectedId" />
            <div class="address-list-heading">
              <h2>{{ t('Saved addresses') }}</h2>
              <button
                class="text-action"
                :disabled="!selectedCustomer()"
                @click="openAddressForm()"
              >
                <Plus :size="14" /> {{ t('Add address') }}
              </button>
            </div>
            <p v-if="addresses.length === 0" class="module-empty">{{ t('No saved addresses') }}</p>
            <article
              v-for="address in addresses"
              :key="address.id"
              class="module-card address-card"
            >
              <div class="address-card-heading">
                <strong
                  >{{ address.recipient_name }} <small>{{ address.phone }}</small></strong
                >
                <span v-if="address.is_default" class="default-address-badge">{{
                  t('Default address')
                }}</span>
              </div>
              <p>
                {{ address.country }} {{ address.province }}{{ address.city }}{{ address.district
                }}{{ address.detail }}
              </p>
              <small v-if="address.longitude != null && address.latitude != null"
                >{{ address.longitude }}, {{ address.latitude }}</small
              >
              <div class="record-actions">
                <button class="text-action" @click="openAddressForm(address)">
                  {{ t('Edit address') }}
                </button>
                <button class="delete-product-button" @click="removeAddress(address)">
                  {{ t('Delete address') }}
                </button>
              </div>
            </article>
          </div>
        </div>
      </section>
    </section>

    <div v-if="formMode" class="product-dialog-backdrop" @click.self="closeForm">
      <section
        class="product-dialog"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="'customer-form-title'"
      >
        <header class="product-dialog-header">
          <div>
            <h2 id="customer-form-title">
              {{
                formMode.startsWith('customer')
                  ? formMode === 'customer-edit'
                    ? t('Edit customer')
                    : t('Add customer')
                  : formMode === 'address-edit'
                    ? t('Edit address')
                    : t('Add address')
              }}
            </h2>
            <p>
              {{
                formMode.startsWith('customer')
                  ? t('Enter customer contact details.')
                  : t('Enter a delivery address. Coordinates are optional.')
              }}
            </p>
          </div>
          <button class="dialog-close" type="button" :aria-label="t('Cancel')" @click="closeForm">
            <X :size="18" />
          </button>
        </header>
        <form
          v-if="formMode.startsWith('customer')"
          class="product-form"
          @submit.prevent="saveCustomer"
        >
          <div class="product-form-grid">
            <label class="form-field form-field-wide"
              ><span>{{ t('Customer name') }}</span
              ><input v-model="customerForm.name" required maxlength="120"
            /></label>
            <label class="form-field"
              ><span>{{ t('Phone') }}</span
              ><input v-model="customerForm.phone" required maxlength="32"
            /></label>
            <label class="form-field"
              ><span>{{ t('Email') }}</span
              ><input v-model="customerForm.email" type="email" maxlength="254"
            /></label>
          </div>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
          <footer class="product-dialog-footer">
            <span></span>
            <div>
              <button class="secondary-action" type="button" :disabled="saving" @click="closeForm">
                {{ t('Cancel') }}</button
              ><button class="primary-action" type="submit" :disabled="saving">
                {{ saving ? t('Saving…') : t('Save changes') }}
              </button>
            </div>
          </footer>
        </form>
        <form v-else class="product-form" @submit.prevent="saveAddress">
          <div class="product-form-grid">
            <label class="form-field"
              ><span>{{ t('Recipient') }}</span
              ><input v-model="addressForm.recipient_name" required maxlength="120"
            /></label>
            <label class="form-field"
              ><span>{{ t('Phone') }}</span
              ><input v-model="addressForm.phone" required maxlength="32"
            /></label>
            <label class="form-field"
              ><span>{{ t('Country') }}</span
              ><input v-model="addressForm.country" required maxlength="80"
            /></label>
            <label class="form-field"
              ><span>{{ t('Province') }}</span
              ><input v-model="addressForm.province" required maxlength="80"
            /></label>
            <label class="form-field"
              ><span>{{ t('City') }}</span
              ><input v-model="addressForm.city" required maxlength="80"
            /></label>
            <label class="form-field"
              ><span>{{ t('District') }}</span
              ><input v-model="addressForm.district" required maxlength="80"
            /></label>
            <label class="form-field form-field-wide"
              ><span>{{ t('Street address') }}</span
              ><input v-model="addressForm.detail" required maxlength="255"
            /></label>
            <label class="form-field"
              ><span>{{ t('Postal code') }}</span
              ><input v-model="addressForm.postal_code" maxlength="20"
            /></label>
            <label class="form-field"
              ><span>{{ t('Longitude') }}</span
              ><input v-model="addressForm.longitude" type="number" step="any" min="-180" max="180"
            /></label>
            <label class="form-field"
              ><span>{{ t('Latitude') }}</span
              ><input v-model="addressForm.latitude" type="number" step="any" min="-90" max="90"
            /></label>
            <label class="checkbox-field form-field-wide"
              ><input v-model="addressForm.is_default" type="checkbox" />
              {{ t('Set as default address') }}</label
            >
          </div>
          <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
          <footer class="product-dialog-footer">
            <span></span>
            <div>
              <button class="secondary-action" type="button" :disabled="saving" @click="closeForm">
                {{ t('Cancel') }}</button
              ><button class="primary-action" type="submit" :disabled="saving">
                {{ saving ? t('Saving…') : t('Save changes') }}
              </button>
            </div>
          </footer>
        </form>
      </section>
    </div>
  </main>
</template>
