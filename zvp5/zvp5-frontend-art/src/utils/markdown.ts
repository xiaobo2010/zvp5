import MarkdownIt from 'markdown-it'
import DOMPurify, { type Config } from 'dompurify'
import { isXuehaiAvailable } from '@/plugins/ua.ts'

/**
 * 渲染上限：超过这个长度的文本只渲染前半截。
 *
 * 超长正文（AI 长回复、整篇文件预览）全量 parse + sanitize 一次要几十毫秒，
 * 还会把 DOM 撑到几千个节点；截断 + 一句提示更划算。
 */
export const MARKDOWN_MAX_CHARS = 20_000

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true
})

/**
 * 表格对齐。
 *
 * markdown-it 默认把对齐写成 style="text-align:center"，而 style 不能进净化
 * 白名单（放开它等于放开任意样式注入）。这里改写成 align 属性：视觉等价，
 * 白名单不放松。class / style / script / iframe 一律仍然挡在门外。
 */
function cellAlignment(attrs: Array<[string, string | number]> | null): string {
  const style = String(attrs?.find(([name]) => name === 'style')?.[1] ?? '')
  const matched = /text-align\s*:\s*(left|center|right)/.exec(style)
  return matched?.[1] ?? ''
}

md.renderer.rules.th_open = (tokens, idx) => {
  const align = cellAlignment(tokens[idx]?.attrs ?? null)
  return `<th${align ? ` align="${align}"` : ''}>`
}

md.renderer.rules.td_open = (tokens, idx) => {
  const align = cellAlignment(tokens[idx]?.attrs ?? null)
  return `<td${align ? ` align="${align}"` : ''}>`
}

const ALLOWED_TAGS = [
  'p',
  'br',
  'strong',
  'b',
  'em',
  'i',
  's',
  'del',
  'code',
  'pre',
  'blockquote',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'a',
  'hr',
  'img',
  // 表格：AI 回复里的对比表很常见，缺了这些标签整张表会被削平成一串段落
  'table',
  'thead',
  'tbody',
  'tfoot',
  'tr',
  'th',
  'td'
]

const ALLOWED_ATTR = ['href', 'title', 'target', 'rel', 'src', 'alt', 'align']

const purifyConfig: Config = {
  ALLOWED_TAGS,
  ALLOWED_ATTR,
  ALLOW_DATA_ATTR: false
}

export function isExternalUrl(raw: string): boolean {
  try {
    const url = new URL(raw, window.location.href)
    return (
      (url.protocol === 'http:' || url.protocol === 'https:') &&
      url.origin !== window.location.origin
    )
  } catch {
    return false
  }
}

function stripExternalImagesOnXuehai(html: string): string {
  if (!html.includes('<img')) return html
  const doc = new DOMParser().parseFromString(html, 'text/html')
  let changed = false
  for (const img of Array.from(doc.body.querySelectorAll('img[src]'))) {
    const src = img.getAttribute('src')
    if (src && isExternalUrl(src)) {
      img.removeAttribute('src')
      changed = true
    }
  }
  return changed ? doc.body.innerHTML : html
}

function sanitizeForRender(html: string): string {
  const clean = DOMPurify.sanitize(html, purifyConfig) as string
  if (!isXuehaiAvailable()) return clean
  return stripExternalImagesOnXuehai(clean)
}

export function renderMarkdown(src: string | undefined | null): string {
  if (!src || src.trim().length === 0) return ''
  return sanitizeForRender(md.render(src))
}

export function renderMarkdownInline(src: string | undefined | null): string {
  if (!src || src.trim().length === 0) return ''
  return sanitizeForRender(md.renderInline(src))
}
