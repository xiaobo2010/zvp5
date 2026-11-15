import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import HomeProgressArtwork from './HomeProgressArtwork.vue'
import type { HomeArtworkAssignment, HomeCategory } from '@/utils/homeArtwork'

const artwork = {
  id: 'test-artwork',
  title: '测试作品',
  artist: '测试作者',
  year: '2026',
  sourceUrl: 'https://example.com/artwork',
  license: 'CC0',
  src: '/artwork-a.jpg',
  width: 123,
  height: 238
}
const assignment: HomeArtworkAssignment = { artwork, seed: 42 }

function category(key: HomeCategory['key'], ratio: number): HomeCategory {
  const labels: Record<HomeCategory['key'], string> = {
    on: '校内义工',
    off: '校外义工',
    social: '社会实践'
  }
  return {
    key,
    label: labels[key],
    raw: ratio * 25,
    effective: ratio * 25,
    target: 25,
    bonus: 0,
    percent: ratio * 100,
    ratio,
    gap: (ratio - 1) * 25
  }
}

const completeCategories = [category('on', 0.25), category('off', 0.5), category('social', 1)]

type LoadHandler = ((event: Event) => void) | null
class FakeImage {
  static instances: FakeImage[] = []
  onload: LoadHandler = null
  onerror: LoadHandler = null
  decoding = ''
  src = ''

  constructor() {
    FakeImage.instances.push(this)
  }

  load() {
    this.onload?.(new Event('load'))
  }

  fail() {
    this.onerror?.(new Event('error'))
  }
}

function createCanvasContext() {
  let currentRects: Array<{ x: number; y: number; width: number; height: number }> = []
  const clipGroups: Array<Array<{ x: number; y: number; width: number; height: number }>> = []
  const drawImage = vi.fn()
  const fillRect = vi.fn()
  const context = {
    fillStyle: '',
    fillRect,
    save: vi.fn(),
    beginPath: vi.fn(),
    rect: vi.fn((x: number, y: number, width: number, height: number) => {
      currentRects.push({ x, y, width, height })
    }),
    clip: vi.fn(() => {
      clipGroups.push(currentRects)
      currentRects = []
    }),
    drawImage,
    restore: vi.fn()
  }
  return { context, clipGroups, drawImage, fillRect }
}

function mediaQuery(options: { legacy?: boolean } = {}) {
  const add = vi.fn()
  const remove = vi.fn()
  const addEventListener = vi.fn()
  const removeEventListener = vi.fn()
  const query = {
    matches: false,
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addListener: add,
    removeListener: remove,
    addEventListener: options.legacy ? undefined : addEventListener,
    removeEventListener: options.legacy ? undefined : removeEventListener,
    dispatchEvent: vi.fn()
  }
  return {
    query: query as unknown as MediaQueryList,
    add,
    remove,
    addEventListener,
    removeEventListener
  }
}

describe('HomeProgressArtwork', () => {
  let contextSpy: ReturnType<typeof vi.spyOn>
  let canvasState: ReturnType<typeof createCanvasContext>

  beforeEach(() => {
    FakeImage.instances = []
    vi.stubGlobal('Image', FakeImage)
    canvasState = createCanvasContext()
    contextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue(canvasState.context as unknown as CanvasRenderingContext2D)
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => mediaQuery().query)
    )
  })

  afterEach(() => {
    contextSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('按物理左中右裁切，三类顺序打乱后仍映射到各自条带', async () => {
    const wrapper = mount(HomeProgressArtwork, {
      props: {
        assignment,
        categories: [completeCategories[2]!, completeCategories[0]!, completeCategories[1]!]
      }
    })
    FakeImage.instances[0]!.load()
    await wrapper.vm.$nextTick()

    expect(canvasState.drawImage).toHaveBeenCalledTimes(3)
    expect(canvasState.clipGroups).toHaveLength(3)
    const bounds = [
      [0, 41],
      [41, 82],
      [82, 123]
    ]
    canvasState.clipGroups.forEach((rects, index) => {
      const [left, right] = bounds[index]!
      const visibleArea = rects.reduce((sum, rect) => sum + rect.width * rect.height, 0)
      expect(rects.length).toBeGreaterThan(0)
      expect(rects.every((rect) => rect.x >= left && rect.x + rect.width <= right)).toBe(true)
      expect(visibleArea).toBe(Math.floor(completeCategories[index]!.ratio * (right - left) * 238))
    })
    expect(canvasState.fillRect).toHaveBeenCalledWith(0, 0, 123, 238)
    wrapper.unmount()
  })

  it('区分未知进度与真实零进度', async () => {
    const wrapper = mount(HomeProgressArtwork, {
      props: { assignment, categories: [], loading: false }
    })
    expect(wrapper.get('canvas').attributes('aria-label')).toContain('等待时长数据')
    expect(wrapper.text()).toContain('等待时长数据')

    await wrapper.setProps({
      categories: [category('on', 0), category('off', 0), category('social', 0)]
    })
    FakeImage.instances[0]!.load()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('从第一次参与开始')
    expect(wrapper.get('canvas').attributes('aria-label')).toContain('揭示0%')
    expect(canvasState.drawImage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('丢弃较早图片请求的迟到回调，只绘制最新作品', async () => {
    const wrapper = mount(HomeProgressArtwork, {
      props: { assignment, categories: completeCategories }
    })
    const first = FakeImage.instances[0]!
    const nextAssignment: HomeArtworkAssignment = {
      seed: 99,
      artwork: { ...artwork, id: 'next-artwork', src: '/artwork-b.jpg' }
    }
    await wrapper.setProps({ assignment: nextAssignment })
    const second = FakeImage.instances[1]!

    first.load()
    await wrapper.vm.$nextTick()
    expect(canvasState.drawImage).not.toHaveBeenCalled()

    second.load()
    await wrapper.vm.$nextTick()
    expect(canvasState.drawImage).toHaveBeenCalledTimes(3)
    expect(canvasState.drawImage.mock.calls.every((call) => call[0] === second)).toBe(true)
    wrapper.unmount()
  })

  it('同源重试不重新分配作品，且仅路径种子变化时保留已加载图片', async () => {
    const wrapper = mount(HomeProgressArtwork, {
      props: { assignment, categories: completeCategories }
    })
    FakeImage.instances[0]!.fail()
    await wrapper.vm.$nextTick()

    const retry = wrapper.get('button')
    await retry.trigger('click')
    expect(FakeImage.instances).toHaveLength(2)
    expect(FakeImage.instances[1]!.src).toBe(artwork.src)

    FakeImage.instances[1]!.load()
    await wrapper.vm.$nextTick()
    const drawsAfterLoad = canvasState.drawImage.mock.calls.length
    await wrapper.setProps({ assignment: { ...assignment, seed: 43 } })
    await wrapper.vm.$nextTick()
    expect(FakeImage.instances).toHaveLength(2)
    expect(canvasState.drawImage.mock.calls.length).toBeGreaterThan(drawsAfterLoad)
    wrapper.unmount()
  })

  it('旧 MediaQueryList 使用 addListener/removeListener 并正确清理', () => {
    const legacy = mediaQuery({ legacy: true })
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => legacy.query)
    )
    const wrapper = mount(HomeProgressArtwork, {
      props: { assignment, categories: completeCategories }
    })

    expect(legacy.add).toHaveBeenCalledTimes(1)
    expect(legacy.addEventListener).not.toHaveBeenCalled()
    wrapper.unmount()
    expect(legacy.remove).toHaveBeenCalledTimes(1)
  })
})
