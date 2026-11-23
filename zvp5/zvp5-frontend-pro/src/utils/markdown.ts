// A small, dependency-free Markdown renderer for notification bodies.
// Raw HTML is escaped before any formatting is applied and link targets are
// restricted to http(s), mailto and same-origin paths.

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function safeUrl(value: string): string {
  const url = value.trim()
  if (/^(https?:|mailto:)/i.test(url) || url.startsWith('/')) return url
  return ''
}

function inline(value: string): string {
  let text = escapeHtml(value)
  const code: string[] = []
  text = text.replace(/`([^`]+)`/g, (_match, content: string) => {
    code.push(`<code>${content}</code>`)
    return `\u0000CODE${code.length - 1}\u0000`
  })

  text = text
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>')

  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, label: string, href: string) => {
    const url = safeUrl(href)
    if (!url) return label
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })

  text = text.replace(/\u0000CODE(\d+)\u0000/g, (_match, index: string) => code[Number(index)] ?? '')
  return text
}

function isBlockStart(line: string): boolean {
  return (
    /^```/.test(line) ||
    /^#{1,6}\s+/.test(line) ||
    /^>\s?/.test(line) ||
    /^[-*+]\s+/.test(line) ||
    /^\d+\.\s+/.test(line) ||
    /^---+$/.test(line)
  )
}

export function renderMarkdown(source: string): string {
  if (!source) return ''
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const output: string[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index] ?? ''
    if (!line.trim()) {
      index += 1
      continue
    }

    const fence = /^```([\w-]*)\s*$/.exec(line)
    if (fence) {
      const language = fence[1] ?? ''
      const body: string[] = []
      index += 1
      while (index < lines.length && !/^```\s*$/.test(lines[index] ?? '')) {
        body.push(lines[index] ?? '')
        index += 1
      }
      index += 1
      output.push(
        `<pre><code${language ? ` class="language-${escapeHtml(language)}"` : ''}>${escapeHtml(body.join('\n'))}</code></pre>`
      )
      continue
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      const level = heading[1]?.length ?? 1
      output.push(`<h${level}>${inline(heading[2] ?? '')}</h${level}>`)
      index += 1
      continue
    }

    if (/^---+$/.test(line.trim())) {
      output.push('<hr />')
      index += 1
      continue
    }

    if (/^>\s?/.test(line)) {
      const quoted: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index] ?? '')) {
        quoted.push((lines[index] ?? '').replace(/^>\s?/, ''))
        index += 1
      }
      output.push(`<blockquote>${inline(quoted.join('\n')).replace(/\n/g, '<br />')}</blockquote>`)
      continue
    }

    const ordered = /^(\d+)\.\s+(.+)$/.exec(line)
    const unordered = /^[-*+]\s+(.+)$/.exec(line)
    if (ordered || unordered) {
      const tag = ordered ? 'ol' : 'ul'
      const items: string[] = []
      while (index < lines.length) {
        const current = lines[index] ?? ''
        const match = ordered ? /^\d+\.\s+(.+)$/.exec(current) : /^[-*+]\s+(.+)$/.exec(current)
        if (!match) break
        items.push(`<li>${inline(match[1] ?? '')}</li>`)
        index += 1
      }
      output.push(`<${tag}>${items.join('')}</${tag}>`)
      continue
    }

    const paragraph: string[] = [line]
    index += 1
    while (index < lines.length && (lines[index] ?? '').trim() && !isBlockStart(lines[index] ?? '')) {
      paragraph.push(lines[index] ?? '')
      index += 1
    }
    output.push(`<p>${inline(paragraph.join('\n')).replace(/\n/g, '<br />')}</p>`)
  }

  return output.join('\n')
}
