import { UAParser } from 'ua-parser-js'

const parser = new UAParser(navigator.userAgent)

const result = parser.getResult()

export function getXuehaiId(): number {
  return window.xhBrowserJava?.getUserId?.() || 0
}

export const pad = () => !!getXuehaiId()

export const isXuehaiDCenter = (requireFeature) => pad && /userId\//.test(navigator.userAgent) && /deviceId\//.test(navigator.userAgent) && (!requireFeature || !!window.webView?.getUserName)

/*export function getTabletType(): 'p615' | 'p620' | 'p200' | 'p355' {
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
}*/
