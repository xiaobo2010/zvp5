import { UAParser } from 'ua-parser-js'

const parser = new UAParser(navigator.userAgent)

const result = parser.getResult()

export const getBrowser = () => result.browser

export const getDevice = () => result.device

export const getEngine = () => result.engine

export const getOS = () => result.os

export const getUA = () => result.ua

export const getCPU = () => result.cpu

export const pad = () => {
  return result.device.vendor === 'Samsung' && result.os.name === 'Android'
}

export function getXuehaiId(): number {
  // 学海浏览器（xhbrowser）：原生桥返回原始用户 id
  const raw = window.xhBrowserJava?.getUserId?.()
  const n = Number(raw)
  if (Number.isFinite(n) && n > 0) return n
  // 资料中心 App（xhdatacenter）：无 xhBrowserJava，UA 形如
  // `deviceId/<devid> userId/<id>`（userId 为原始用户 id）
  const m = /userId\/(\w+)/.exec(navigator.userAgent)
  const u = m ? Number(m[1]) : NaN
  return Number.isFinite(u) && u > 0 ? u : 0
}

export const isXuehaiAvailable = () => getXuehaiId() > 0

export function getTabletType(): 'p615' | 'p620' | 'p200' | 'p355' {
  const model = result.device.model
  if (!pad()) return 'p620'
  switch (model) {
    case 'SM-P620':
      return 'p620'
    case 'SM-P615C':
      return 'p615'
    case 'SM-P200':
      return 'p200'
    case 'SM-P355C':
      return 'p355'
    default:
      return 'p620'
  }
}
