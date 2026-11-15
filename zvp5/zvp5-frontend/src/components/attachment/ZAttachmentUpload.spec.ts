import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ZAttachmentUpload from './ZAttachmentUpload.vue'
import type { AttachmentMeta } from '@/../types'

/**
 * 删除链路：后端 DELETE /attachments/{oid} 无条件要求密码再认证（body 里的
 * verification_token），所以点击删除必须先换一张 verify 短令牌；用户取消密码框
 * 就什么都不发，条目原样保留。
 */

const { deleteOneSpy, getMetaSpy, getThumbSpy, uploadSpy } = vi.hoisted(() => ({
  deleteOneSpy: vi.fn(),
  getMetaSpy: vi.fn(),
  getThumbSpy: vi.fn(),
  uploadSpy: vi.fn()
}))

vi.mock('@/api/attachment', () => ({
  upload: uploadSpy,
  read: {
    getMeta: getMetaSpy,
    getThumb: getThumbSpy,
    getBytes: vi.fn()
  },
  manage: {
    deleteOne: deleteOneSpy
  },
  audit: {}
}))

const shortToken = vi.hoisted(() => ({
  withToken: vi.fn(),
  cancelled: vi.fn()
}))

vi.mock('@/plugins/short-token', () => ({
  withTemporaryToken: shortToken.withToken,
  isTemporaryTokenCancelled: shortToken.cancelled,
  cachedTemporaryToken: vi.fn(() => ''),
  temporaryToken: vi.fn(),
  clearTemporaryToken: vi.fn()
}))

vi.mock('@/plugins/device-unlock', () => ({
  ensureDeviceUnlock: vi.fn(async () => ''),
  deviceLocked: () => false,
  cachedUnlockToken: () => '',
  clearDeviceUnlock: vi.fn(),
  DEVICE_LOCK_DETAIL: '学海设备需要密码解锁后访问媒体库',
  DEVICE_UNLOCK_REASON: '平板访问附件需要验证密码，解锁后 5 分钟内有效'
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ _id: 'user-oid', position: ['student'] })
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key
  })
}))

vi.mock('@/api/ai/ocr', () => ({ enqueueOcr: vi.fn() }))

const polling = vi.hoisted(() => ({
  start: vi.fn(),
  stop: vi.fn(),
  stopAll: vi.fn()
}))

vi.mock('@/components/ai/useAiOcrPolling', () => ({
  useAiOcrPolling: () => ({
    start: polling.start,
    stop: polling.stop,
    stopAll: polling.stopAll,
    isRunning: () => false,
    running: { value: [] }
  })
}))

vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const ElButton = defineComponent({
    name: 'ElButton',
    props: { type: String, plain: Boolean, size: String },
    emits: ['click'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'button',
          { class: 'el-button-stub', onClick: () => emit('click') },
          slots.default?.()
        )
    }
  })
  const ElIcon = defineComponent({
    name: 'ElIcon',
    setup(_, { slots }) {
      return () => h('span', { class: 'el-icon-stub' }, slots.default?.())
    }
  })
  const ElDialog = defineComponent({
    name: 'ElDialog',
    props: { modelValue: Boolean },
    setup(props, { slots }) {
      return () => (props.modelValue ? h('div', { class: 'el-dialog-stub' }, slots.default?.()) : null)
    }
  })
  const ElImageViewer = defineComponent({
    name: 'ElImageViewer',
    props: { urlList: { type: Array, default: () => [] } },
    setup() {
      return () => h('div', { class: 'el-image-viewer-stub' })
    }
  })
  const ElProgress = defineComponent({
    name: 'ElProgress',
    props: { percentage: Number },
    setup() {
      return () => h('div', { class: 'el-progress-stub' })
    }
  })
  return {
    ElButton,
    ElIcon,
    ElDialog,
    ElImageViewer,
    ElProgress,
    ElMessage: Object.assign(vi.fn(), {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
      info: vi.fn()
    }),
    ElMessageBox: { confirm: vi.fn() }
  }
})

const metaFixture: AttachmentMeta = {
  _id: 'oid-1',
  mime: 'image/jpeg',
  size: 1024,
  ocr_status: 'done',
  status: 'ready',
  purpose: 'volunteer_order',
  original_name: '义工单.jpg',
  created_at: '2026-10-20T08:00:00'
}

function mountUpload(): VueWrapper {
  return mount(ZAttachmentUpload, {
    props: { modelValue: ['oid-1'] }
  })
}

beforeEach(() => {
  deleteOneSpy.mockReset().mockResolvedValue(undefined)
  getMetaSpy.mockReset().mockResolvedValue(structuredClone(metaFixture))
  getThumbSpy.mockReset().mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }))
  uploadSpy.mockReset()
  polling.start.mockReset()
  polling.stop.mockReset()
  polling.stopAll.mockReset()
  shortToken.cancelled.mockReset().mockImplementation((error: unknown) => error === 'cancel')
  shortToken.withToken
    .mockReset()
    .mockImplementation(
      async (
        _userid: string,
        _reason: string,
        action: (token: string) => Promise<unknown>
      ) => action('tok-verify')
    )
  Object.defineProperty(URL, 'createObjectURL', {
    writable: true,
    value: vi.fn(() => 'blob:mock')
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    writable: true,
    value: vi.fn()
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('deletes an attachment only after the password re-auth token arrives', async () => {
  const wrapper = mountUpload()
  await flushPromises()

  await wrapper.find('.yg-attachment-upload__icon-btn--danger').trigger('click')
  await flushPromises()

  expect(shortToken.withToken).toHaveBeenCalledTimes(1)
  expect(shortToken.withToken.mock.calls[0]![0]).toBe('user-oid')
  expect(shortToken.withToken.mock.calls[0]![1]).toBe('manage.attachments.deleteVerifyReason')
  expect(deleteOneSpy).toHaveBeenCalledWith('oid-1', 'tok-verify')
  expect(wrapper.emitted('deleted')).toEqual([['oid-1']])
  expect(wrapper.emitted('update:modelValue')).toEqual([[[]]])

  wrapper.unmount()
})

it('does not send the delete request when the password prompt is cancelled', async () => {
  shortToken.withToken.mockRejectedValue('cancel')

  const wrapper = mountUpload()
  await flushPromises()

  await wrapper.find('.yg-attachment-upload__icon-btn--danger').trigger('click')
  await flushPromises()

  expect(deleteOneSpy).not.toHaveBeenCalled()
  expect(wrapper.emitted('deleted')).toBeUndefined()
  // 取消不是错误：不往父级抛 error，条目仍在页面上
  expect(wrapper.emitted('error')).toBeUndefined()
  expect(wrapper.findAll('.yg-attachment-upload__icon-btn--danger').length).toBe(1)

  wrapper.unmount()
})

it('keeps the item and reports the error when the delete itself fails', async () => {
  const failure = new Error('boom')
  deleteOneSpy.mockRejectedValue(failure)

  const wrapper = mountUpload()
  await flushPromises()

  await wrapper.find('.yg-attachment-upload__icon-btn--danger').trigger('click')
  await flushPromises()

  expect(deleteOneSpy).toHaveBeenCalledWith('oid-1', 'tok-verify')
  expect(wrapper.emitted('error')).toEqual([[failure]])
  expect(wrapper.emitted('deleted')).toBeUndefined()
  expect(wrapper.findAll('.yg-attachment-upload__icon-btn--danger').length).toBe(1)

  wrapper.unmount()
})
