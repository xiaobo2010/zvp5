import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { zhCN } from '@/i18n/locales'
import VolunteerRegisterEntry from './VolunteerRegisterEntry.vue'

// 操作选择页只负责分流：这里锁住两个入口的文案与目标 URL、
// query 的保留，以及「没有应用内上一页时」的安全回退。
vi.mock('./registerAccess', () => ({
  ensureRegisterAccess: vi.fn(async () => true)
}))

const Page = defineComponent({ render: () => h('div') })

function buildRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/activity/register', name: 'activity-register', component: Page },
      {
        path: '/activity/register/:mode(create|edit)',
        name: 'activity-register-mode',
        component: Page
      },
      { path: '/activities/mine', name: 'activity', component: Page }
    ]
  })
}

async function mountEntry(url = '/activity/register') {
  const router = buildRouter()
  await router.push(url)
  await router.isReady()
  const pinia = createPinia()
  const i18n = createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN } })
  const wrapper = mount(VolunteerRegisterEntry, {
    global: { plugins: [router, pinia, i18n] }
  })
  return { wrapper, router }
}

function backButton(wrapper: VueWrapper) {
  return wrapper.get('button.yg-btn--ghost')
}

describe('VolunteerRegisterEntry', () => {
  it('offers one visible choice per mode', async () => {
    const { wrapper } = await mountEntry()

    expect(wrapper.text()).toContain(zhCN.nav.registerEntry.create.title)
    expect(wrapper.text()).toContain(zhCN.nav.registerEntry.edit.title)

    const links = wrapper.findAll('a.yg-register-entry__choice')
    expect(links).toHaveLength(2)
    expect(links[0].classes()).toContain('yg-register-entry__choice--create')
    expect(links[1].classes()).toContain('yg-register-entry__choice--edit')
    expect(links[0].attributes('href')).toBe('/activity/register/create')
    expect(links[1].attributes('href')).toBe('/activity/register/edit')
  })

  it('keeps the query when moving into the editor', async () => {
    const { wrapper } = await mountEntry('/activity/register?from=list&session=s1')

    const links = wrapper.findAll('a.yg-register-entry__choice')
    expect(links[0].attributes('href')).toBe('/activity/register/create?from=list&session=s1')
    expect(links[1].attributes('href')).toBe('/activity/register/edit?from=list&session=s1')
  })

  it('falls back to the activity list when there is no in-app history', async () => {
    const { wrapper, router } = await mountEntry('/activity/register?search=abc')
    // 冷启动直接打开本页：历史里没有应用内上一页。
    router.options.history.state.back = null

    await backButton(wrapper).trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/activities/mine')
    expect(router.currentRoute.value.query).toEqual({ search: 'abc' })
  })

  it('uses router.back when an in-app route precedes the chooser', async () => {
    const { wrapper, router } = await mountEntry()
    router.options.history.state.back = '/activities/mine'
    const backSpy = vi.spyOn(router, 'back').mockImplementation(() => {})

    await backButton(wrapper).trigger('click')

    expect(backSpy).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.path).toBe('/activity/register')
  })
})
