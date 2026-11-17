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
  const drawImage = vi.fn()
  const fillRect = vi.fn()
  const putImageData = vi.fn()
  const getImageData = vi.fn((_x: number, _y: number, width: number, height: number) => {
    const data = new Uint8ClampedArray(width * height * 4)
    for (let i = 0; i < data.length; i += 4) {
      data[i] = 10
      data[i + 1] = 20
      data[i + 2] = 30
      data[i + 3] = 255
    }
    return { data, width, height, colorSpace: 'srgb' } as ImageData
  })
  const createImageData = vi.fn((width: number, height: number) => ({
    data: new Uint8ClampedArray(width * height * 4),
    width,
    height,
    colorSpace: 'srgb'
  }))
  const context = {
    fillStyle: '',
    fillRect,
    drawImage,
    getImageData,
    createImageData,
    putImageData
  }
  return { context, drawImage, fillRect, getImageData, putImageData }
}

function revealedPixels(context: ReturnType<typeof createCanvasContext>) {
  return context.putImageData.mock.lastCall?.[0].data as Uint8ClampedArray | undefined
}

function countChannelValue(pixels: Uint8ClampedArray, channel: number, value: number): number {
  let count = 0
  for (let i = channel; i < pixels.length; i += 4) if (pixels[i] === value) count++
  return count
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

  it('按全图像素比例点亮对应 RGB 通道，顺序打乱也按类别映射', async () => {
    const wrapper = mount(HomeProgressArtwork, {
      props: {
        assignment,
        categories: [completeCategories[2]!, completeCategories[0]!, completeCategories[1]!]
      }
    })
    FakeImage.instances[0]!.load()
    await wrapper.vm.$nextTick()

    const totalPixels = 123 * 238
    const pixels = revealedPixels(canvasState)
    expect(canvasState.drawImage).toHaveBeenCalledTimes(1)
    expect(pixels).toHaveLength(totalPixels * 4)
    expect(countChannelValue(pixels!, 0, 10)).toBe(Math.floor(0.25 * totalPixels))
    expect(countChannelValue(pixels!, 1, 20)).toBe(Math.floor(0.5 * totalPixels))
    expect(countChannelValue(pixels!, 2, 30)).toBe(totalPixels)
    for (let i = 3; i < pixels!.length; i += 4) expect(pixels![i]).toBe(255)
    wrapper.unmount()
  })

  it('百分比只决定点亮像素数，达到 100% 时恢复原图全部通道', async () => {
    const wrapper = mount(HomeProgressArtwork, {
      props: {
        assignment,
        categories: [category('on', 1), category('off', 1), category('social', 1)]
      }
    })
    FakeImage.instances[0]!.load()
    await wrapper.vm.$nextTick()

    const pixels = revealedPixels(canvasState)!
    expect(countChannelValue(pixels, 0, 10)).toBe(123 * 238)
    expect(countChannelValue(pixels, 1, 20)).toBe(123 * 238)
    expect(countChannelValue(pixels, 2, 30)).toBe(123 * 238)
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
    expect(wrapper.get('canvas').attributes('aria-label')).toContain('点亮全图0%像素')
    expect(canvasState.putImageData).not.toHaveBeenCalled()
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
    expect(canvasState.putImageData).not.toHaveBeenCalled()

    second.load()
    await wrapper.vm.$nextTick()
    expect(canvasState.drawImage).toHaveBeenCalledTimes(1)
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
