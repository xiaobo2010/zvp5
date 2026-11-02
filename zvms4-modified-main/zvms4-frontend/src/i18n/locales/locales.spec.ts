import { describe, expect, it } from 'vitest'
import { baseCompile } from '@intlify/message-compiler'
import { createI18n } from 'vue-i18n'
import { zhCN } from './index'

function leaves(value: unknown, out: Array<{ key: string; message: string }> = []) {
  if (typeof value === 'string') {
    return out
  }
  if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      if (typeof child === 'string') {
        out.push({ key, message: child })
      } else if (child && typeof child === 'object') {
        leaves(child, out)
      }
    }
  }
  return out
}

// vue-i18n 的消息体同时也是模板语法:`@` 是链接消息前缀,`|` 是复数分隔,
// `{}` 是插值。文案里出现裸 `@`(典型是 root@host)时,开发构建会把编译错误
// 打到 console 后回退原文,生产构建则直接抛 CompileError —— 本轮 SSH 终端页
// 就是 `root@127.0.0.1` 让 t('server.ssh.authScene') 抛错、授权框永远打不开。
// 这里直接用编译器收集每条文案的 onError,不依赖运行时是否回退。
describe('locale messages', () => {
  const messages = leaves(zhCN)

  it('every message compiles without syntax errors', () => {
    const failures: string[] = []
    for (const { key, message } of messages) {
      const errors: string[] = []
      baseCompile(message, {
        onError: (error) => errors.push(`${error.name}:${error.message}`)
      })
      if (errors.length > 0) {
        failures.push(`${key} => ${errors.join(' | ')}`)
      }
    }
    expect(failures).toEqual([])
  })

  it('renders the SSH auth scene with the selected target', () => {
    const i18n = createI18n({
      locale: 'zh-CN',
      fallbackLocale: 'zh-CN',
      messages: { 'zh-CN': zhCN },
      legacy: false
    })
    // 目标展示串由服务端下发,可能自带 @;插值数据不参与模板解析。
    expect(i18n.global.t('server.ssh.authScene', { target: 'root@192.0.2.11' })).toContain(
      'root@192.0.2.11'
    )
  })
})
