import type { AiChatMessage } from '@/../types/ai'

/**
 * Markdown 写作助手的提示词构造。
 *
 * 单独成文件是为了可测：提示词里最容易出错的是「模型回了半句解释」和「把整段
 * 结果包进 ```」，这两件事全靠 system 约束，跟界面无关。纯函数不碰网络，
 * 单测直接断言消息体。
 */

export type AiWriteAction = 'polish' | 'expand' | 'shorten' | 'summary' | 'continue'

export const AI_WRITE_ACTIONS: readonly AiWriteAction[] = [
  'polish',
  'expand',
  'shorten',
  'summary',
  'continue'
]

const SYSTEM_PROMPT = [
  '你是中文写作助手，负责改写 Markdown 文本。',
  '只输出改写后的正文：不要解释你的做法，不要加前后缀，不要用代码围栏包住整段结果（原文本身就是代码时除外）。',
  '保留原文里的链接、代码、专有名词和 Markdown 结构，除非要求改变。'
].join('\n')

const ACTION_PROMPTS: Record<AiWriteAction, string> = {
  polish: '润色下面的文字：保持原意与信息量，改善表达与流畅度，不要扩写。',
  expand: '在保持原意的前提下扩写下面的文字：补充必要的细节与过渡，不要编造事实。',
  shorten: '精简下面的文字：保留全部关键信息，删掉重复与冗余。',
  summary: '把下面的内容概括成 Markdown 要点列表，只保留关键结论。',
  continue: '顺着下面的内容继续写：保持原有语气、结构与 Markdown 风格，只输出新增部分。'
}

export interface AiPromptInput {
  action: AiWriteAction
  source: string
  instruction?: string
}

export function buildAiMessages({ action, source, instruction }: AiPromptInput): AiChatMessage[] {
  const parts = [ACTION_PROMPTS[action]]
  const extra = (instruction ?? '').trim()
  if (extra) {
    parts.push(`额外要求：${extra}`)
  }
  const body = source.trim()
  parts.push(body ? `原文：\n${source}` : '原文为空，请根据操作与额外要求直接生成。')

  return [
    { role: 'system', content: SYSTEM_PROMPT },
    { role: 'user', content: parts.join('\n\n') }
  ]
}

/**
 * 模型偶尔仍会整段包一层 ``` 围栏。剥掉它，但不能误伤「原文本来就是代码块」：
 * 只有首尾各有一行围栏、且中间内容不以围栏开头时才剥。
 */
export function stripWrappingFence(text: string): string {
  const trimmed = text.trim()
  const match = /^```[^\n]*\n([\s\S]*?)\n```$/.exec(trimmed)
  if (!match) {
    return text
  }
  return match[1]
}
