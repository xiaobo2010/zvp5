import MarkdownIt from 'markdown-it'
import DOMPurify, { type Config } from 'dompurify'
import { isXuehaiAvailable } from '@/plugins/ua.ts'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true
})

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
  'img'
]

const ALLOWED_ATTR = ['href', 'title', 'target', 'rel', 'src', 'alt']

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
