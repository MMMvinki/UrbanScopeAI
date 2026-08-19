import type { ReactNode } from 'react'

/**
 * 安全迷你 Markdown 渲染器（对齐流式消息正文的观感）。
 *
 * 只支持结论正文常见语法：段落 / 标题(#~###) / 无序列表(-) / 有序列表(1.) /
 * 行内加粗(**x**) / 行内代码(`x`) / 斜体(*x*) / 分隔线(---) / 代码围栏(```)。
 *
 * 全程产出 React 节点，不使用 dangerouslySetInnerHTML——LLM 输出可能含
 * HTML/脚本，绝不能原样注入。
 */

/** 行内样式 → React 节点（**加粗** / *斜体* / `行内代码`） */
function renderInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g
  let last = 0
  let k = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    const token = m[0]
    if (token.startsWith('**') && token.endsWith('**') && token.length > 4) {
      nodes.push(<strong key={`${keyBase}:${k++}`}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('`') && token.endsWith('`') && token.length > 2) {
      nodes.push(
        <code key={`${keyBase}:${k++}`} className="chat-md__code">
          {token.slice(1, -1)}
        </code>,
      )
    } else if (token.startsWith('*') && token.endsWith('*') && token.length > 2) {
      nodes.push(<em key={`${keyBase}:${k++}`}>{token.slice(1, -1)}</em>)
    } else {
      nodes.push(token)
    }
    last = m.index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

const BLOCK_STARTS = /^\s*(?:```|#{1,3}\s+|[-*]\s+|\d+[.)]\s+|-{3,}|\*{3,}|_{3,})/

/** 收集段落：连续非空且非块起始的行 */
function collectParagraph(lines: string[], start: number): { text: string; next: number } {
  const para: string[] = []
  let i = start
  while (
    i < lines.length &&
    lines[i].trim() !== '' &&
    !BLOCK_STARTS.test(lines[i])
  ) {
    para.push(lines[i])
    i++
  }
  return { text: para.join('\n'), next: i }
}

export function MarkdownText({ text }: { text: string }) {
  const lines = text.split(/\r?\n/)
  const blocks: ReactNode[] = []
  let i = 0
  let k = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    // 代码围栏
    if (/^```/.test(trimmed)) {
      const code: string[] = []
      i++
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        code.push(lines[i])
        i++
      }
      i++ // 跳过收尾围栏
      blocks.push(
        <pre key={k++} className="chat-md__fence">
          <code>{code.join('\n')}</code>
        </pre>,
      )
      continue
    }

    // 标题
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed)
    if (heading) {
      const level = Math.min(heading[1].length + 3, 6)
      const Tag = `h${level}` as 'h4' | 'h5' | 'h6'
      blocks.push(
        <Tag key={k++} className="chat-md__heading">
          {renderInline(heading[2], `h${k}`)}
        </Tag>,
      )
      i++
      continue
    }

    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push(<hr key={k++} className="chat-md__hr" />)
      i++
      continue
    }

    // 无序列表
    if (/^\s*[-*]\s+/.test(line)) {
      const items: ReactNode[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        const content = lines[i].replace(/^\s*[-*]\s+/, '')
        items.push(<li key={items.length}>{renderInline(content, `b${k}-${items.length}`)}</li>)
        i++
      }
      blocks.push(
        <ul key={k++} className="chat-md__list">
          {items}
        </ul>,
      )
      continue
    }

    // 有序列表
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: ReactNode[] = []
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        const content = lines[i].replace(/^\s*\d+[.)]\s+/, '')
        items.push(<li key={items.length}>{renderInline(content, `o${k}-${items.length}`)}</li>)
        i++
      }
      blocks.push(
        <ol key={k++} className="chat-md__list">
          {items}
        </ol>,
      )
      continue
    }

    // 空行
    if (trimmed === '') {
      i++
      continue
    }

    // 段落
    const para = collectParagraph(lines, i)
    if (para.text.trim()) {
      blocks.push(
        <p key={k++} className="chat-md__p">
          {renderInline(para.text, `p${k}`)}
        </p>,
      )
    }
    i = para.next
  }

  return <div className="chat-md">{blocks}</div>
}
