import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { h } from 'vue'
import ServerFiles from './ServerFiles.vue'
import { listFiles } from '@/api/system/files.ts'

const { elMessageSpy, elementComponents } = vi.hoisted(() => {
  const ElCard = {
    name: 'ElCard',
    template: '<section class="el-card-stub"><slot name="header" /><slot /></section>'
  }
  const ElButton = {
    name: 'ElButton',
    props: { disabled: Boolean, type: String, size: String },
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>'
  }
  const ElInput = {
    name: 'ElInput',
    props: { modelValue: [String, Number] },
    emits: ['update:modelValue', 'keyup'],
    template:
      '<input class="el-input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @keyup="$emit(\'keyup\', $event)" />'
  }
  const ElMenu = {
    name: 'ElMenu',
    props: { defaultActive: String },
    emits: ['select'],
    template: '<nav class="el-menu-stub"><slot /></nav>'
  }
  const ElMenuItem = {
    name: 'ElMenuItem',
    props: { index: String },
    emits: ['click'],
    template: '<div class="el-menu-item-stub" @click="$emit(\'click\')"><slot /></div>'
  }
  const ElIcon = { name: 'ElIcon', template: '<span><slot /></span>' }
  const ElBreadcrumb = { name: 'ElBreadcrumb', template: '<nav><slot /></nav>' }
  const ElBreadcrumbItem = {
    name: 'ElBreadcrumbItem',
    emits: ['click'],
    template: '<span class="crumb-stub" @click="$emit(\'click\')"><slot /></span>'
  }
  const ElTable = { name: 'ElTable', template: '<div class="el-table-stub"><slot /></div>' }
  const ElTableColumn = {
    name: 'ElTableColumn',
    props: {
      label: String,
      width: [Number, String],
      minWidth: [Number, String],
      fixed: [String, Boolean]
    },
    template: '<div class="el-table-column-stub" :data-label="label" :data-width="width" />'
  }
  const ElDropdown = { name: 'ElDropdown', template: '<div><slot /><slot name="dropdown" /></div>' }
  const ElDropdownMenu = { name: 'ElDropdownMenu', template: '<div><slot /></div>' }
  const ElDropdownItem = { name: 'ElDropdownItem', template: '<div><slot /></div>' }
  const ElEmpty = { name: 'ElEmpty', props: { description: String }, template: '<div />' }
  return {
    elMessageSpy: vi.fn(),
    elementComponents: {
      ElCard,
      ElButton,
      ElInput,
      ElMenu,
      ElMenuItem,
      ElIcon,
      ElBreadcrumb,
      ElBreadcrumbItem,
      ElTable,
      ElTableColumn,
      ElDropdown,
      ElDropdownMenu,
      ElDropdownItem,
      ElEmpty
    }
  }
})

vi.mock('element-plus', () => ({
  ...elementComponents,
  ElLoadingDirective: { mounted: () => undefined, updated: () => undefined },
  ElMessage: Object.assign(elMessageSpy, {
    success: elMessageSpy,
    error: elMessageSpy,
    warning: elMessageSpy,
    info: elMessageSpy,
    closeAll: vi.fn()
  }),
  ElMessageBox: { confirm: vi.fn(), prompt: vi.fn() }
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key })
}))

// jsdom 没有 matchMedia:宽屏断点恒为真,竖屏断点恒为假,表格走完整五列分支。
vi.mock('@vueuse/core', () => ({
  useMediaQuery: (query: string) => query.includes('min-width')
}))

vi.mock('@/api/system/files.ts', () => ({
  listFiles: vi.fn(),
  makeDir: vi.fn(),
  uploadFile: vi.fn(),
  renamePath: vi.fn(),
  deleteEntry: vi.fn(),
  chmodEntry: vi.fn(),
  downloadFile: vi.fn()
}))

vi.mock('@/components/system/ZSystemAuthDialog.vue', () => ({
  default: {
    name: 'ZSystemAuthDialogStub',
    setup(_props: unknown, { expose }: { expose: (api: Record<string, unknown>) => void }) {
      expose({ open: vi.fn(), succeed: vi.fn(), fail: vi.fn() })
      return () => h('div', { class: 'auth-dialog-stub' })
    }
  }
}))

vi.mock('@/components/server/ZFilePreview.vue', () => ({
  default: { name: 'ZFilePreviewStub', template: '<div />' }
}))

vi.mock('@/components/utils/ZPagination.vue', () => ({
  default: { name: 'ZPaginationStub', template: '<div />' }
}))

function listResponse(path: string) {
  return {
    data: {
      status: 'ok',
      code: 200,
      data: { path, offset: 0, limit: 200, total: 0, entries: [] }
    }
  }
}

function mountPage(): VueWrapper {
  return mount(ServerFiles, {
    global: { directives: { loading: {} } }
  })
}

beforeEach(() => {
  vi.mocked(listFiles).mockReset()
  vi.mocked(listFiles).mockImplementation(async (path: string) => listResponse(path) as never)
  elMessageSpy.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

it('renders only the two allowed roots and starts at /www/zvms4', async () => {
  const wrapper = mountPage()
  await flushPromises()

  expect(listFiles).toHaveBeenCalledTimes(1)
  expect(listFiles).toHaveBeenCalledWith('/www/zvms4', 0, 200)

  const quick = wrapper.find('.quick-menu').text()
  expect(quick).toContain('/www/zvms4')
  expect(quick).toContain('/home')
  expect(quick).not.toContain('/etc')
  expect(quick).not.toContain('/var/log')

  wrapper.unmount()
})

it('blocks a typed path outside the roots and keeps the current path', async () => {
  const wrapper = mountPage()
  await flushPromises()

  const input = wrapper.find('.el-input-stub')
  await input.setValue('/etc/passwd')
  await input.trigger('keyup.enter')
  await flushPromises()

  expect(elMessageSpy).toHaveBeenCalledTimes(1)
  expect(elMessageSpy.mock.calls[0][0]).toMatchObject({
    message: 'server.files.rootForbidden',
    type: 'warning'
  })
  expect(listFiles).toHaveBeenCalledTimes(1)
  expect((input.element as HTMLInputElement).value).toBe('/www/zvms4')

  wrapper.unmount()
})

it('does not treat /home2 as being inside /home', async () => {
  const wrapper = mountPage()
  await flushPromises()

  const input = wrapper.find('.el-input-stub')
  await input.setValue('/home2/secret')
  await input.trigger('keyup.enter')
  await flushPromises()

  expect(elMessageSpy.mock.calls[0][0]).toMatchObject({ message: 'server.files.rootForbidden' })
  expect(listFiles).toHaveBeenCalledTimes(1)

  wrapper.unmount()
})

it('follows a typed path under /home', async () => {
  const wrapper = mountPage()
  await flushPromises()

  const input = wrapper.find('.el-input-stub')
  await input.setValue('/home/logs/')
  await input.trigger('keyup.enter')
  await flushPromises()

  expect(elMessageSpy).not.toHaveBeenCalled()
  expect(listFiles).toHaveBeenCalledTimes(2)
  expect(listFiles).toHaveBeenLastCalledWith('/home/logs', 0, 200)
  expect((input.element as HTMLInputElement).value).toBe('/home/logs')

  wrapper.unmount()
})

// 宽屏四个带 icon 的文字按钮(下载/重命名/权限/删除)约 265px 宽,
// 旧的 250px 列宽 + overflow:hidden 会把最左边的「下载」裁掉一半。
it('reserves enough width for the four wide-screen row actions', async () => {
  const wrapper = mountPage()
  await flushPromises()

  const column = wrapper
    .findAll('.el-table-column-stub')
    .find((item) => item.attributes('data-label') === 'server.files.colActions')

  expect(column).toBeTruthy()
  expect(column!.attributes('data-width')).toBe('320')

  wrapper.unmount()
})
