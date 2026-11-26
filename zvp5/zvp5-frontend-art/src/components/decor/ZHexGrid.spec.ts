import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ZHexGrid from './ZHexGrid.vue'

describe('ZHexGrid', () => {
  it('默认不挂扫光类，开启 sweep 后才启用纵向亮带', () => {
    const plain = mount(ZHexGrid)
    expect(plain.find('.z-hex-grid').classes()).not.toContain('z-hex-grid--sweep')
    plain.unmount()

    const swept = mount(ZHexGrid, { props: { sweep: true, drift: true } })
    const root = swept.find('.z-hex-grid')
    expect(root.classes()).toContain('z-hex-grid--sweep')
    expect(root.classes()).toContain('z-hex-grid--drift')
    // 蜂窝路径仍然只生成一份，扫光纯 CSS，不额外画 canvas
    expect(swept.find('path').attributes('d')).toContain('M')
    swept.unmount()
  })
})
