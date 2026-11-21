import { createI18n } from 'vue-i18n'
import { messages } from './messages'

const stored = (() => {
  try {
    return localStorage.getItem('zvp5.locale')
  } catch {
    return null
  }
})()

export const i18n = createI18n({
  legacy: false,
  locale: stored === 'en-US' ? 'en-US' : 'zh-CN',
  fallbackLocale: 'zh-CN',
  messages
})
