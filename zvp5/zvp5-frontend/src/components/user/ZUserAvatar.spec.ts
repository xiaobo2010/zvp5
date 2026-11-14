import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ZUserAvatar from './ZUserAvatar.vue'

describe('ZUserAvatar', () => {
  it('取姓名首字，整块对读屏隐藏', () => {
    const wrapper = mount(ZUserAvatar, { props: { name: '张三', code: '20240018', size: 'md' } })
    const plate = wrapper.find('.z-pilot-plate')

    expect(plate.attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('.z-pilot-plate__type').text()).toBe('张')
    wrapper.unmount()
  })

  it('空名字回退问号', () => {
    const wrapper = mount(ZUserAvatar, { props: { name: '   ', code: '' } })

    expect(wrapper.find('.z-pilot-plate__type').text()).toBe('?')
    wrapper.unmount()
  })

  it('按 size 挂对应尺寸 class，xs 不画短号', () => {
    const xs = mount(ZUserAvatar, { props: { name: '李', code: '7', size: 'xs' } })
    const lg = mount(ZUserAvatar, { props: { name: '李', code: '7', size: 'lg' } })

    expect(xs.find('.z-pilot-plate').classes()).toContain('z-pilot-plate--xs')
    expect(xs.find('.z-pilot-plate__serial').exists()).toBe(false)
    expect(lg.find('.z-pilot-plate').classes()).toContain('z-pilot-plate--lg')
    expect(lg.find('.z-pilot-plate__serial').exists()).toBe(true)

    xs.unmount()
    lg.unmount()
  })

  it('同一学号得到同一短号，不同学号不撞号', () => {
    const first = mount(ZUserAvatar, { props: { name: '张', code: '20240018', size: 'md' } })
    const same = mount(ZUserAvatar, { props: { name: '张', code: '20240018', size: 'lg' } })
    const other = mount(ZUserAvatar, { props: { name: '张', code: '20249999', size: 'md' } })

    expect(first.find('.z-pilot-plate').attributes('data-serial')).toBe(
      same.find('.z-pilot-plate').attributes('data-serial')
    )
    expect(first.find('.z-pilot-plate').attributes('data-serial')).not.toBe(
      other.find('.z-pilot-plate').attributes('data-serial')
    )

    first.unmount()
    same.unmount()
    other.unmount()
  })
})
