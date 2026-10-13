import MarkdownIt from 'markdown-it'
import DOMPurify, { type Config } from 'dompurify'

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

export function renderMarkdown(src: string | undefined | null): string {
  if (!src || src.trim().length === 0) return ''
  return DOMPurify.sanitize(md.render(src), purifyConfig) as string
}

export function renderMarkdownInline(src: string | undefined | null): string {
  if (!src || src.trim().length === 0) return ''
  return DOMPurify.sanitize(md.renderInline(src), purifyConfig) as string
}
