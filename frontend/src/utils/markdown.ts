/**
 * A small Markdown parser, for one document.
 *
 * The user manual is written in Markdown and shipped as Markdown
 * (src/content/USER_MANUAL.md) so that it stays readable and diffable in the
 * repository; the Help page renders that same file rather than a hand-kept
 * copy of it, which is the only arrangement under which the manual and the
 * app cannot say different things.
 *
 * Rendering it needs a parser, and this is one instead of a dependency
 * because the document uses a knowable subset of Markdown -- headings, lists,
 * tables, fenced blocks, and four kinds of inline mark -- and a general
 * CommonMark implementation plus its plugins is a large amount of code and
 * supply chain to carry for a single file whose syntax we control. What is
 * NOT supported is therefore a real constraint on the manual: no raw HTML, no
 * images, no blockquotes, no setext headings, no reference-style links, and
 * no underscore emphasis (`sample_name` and `age_bp` are column names in that
 * document, and are meant to survive).
 *
 * Parsing is separated from rendering so the structure can be asserted in a
 * plain node test -- see markdown.test.ts -- rather than only by looking at
 * the page.
 */

export type Inline =
  | { type: 'text'; value: string }
  | { type: 'code'; value: string }
  | { type: 'strong'; children: Inline[] }
  | { type: 'em'; children: Inline[] }
  | { type: 'link'; href: string; children: Inline[] }

export interface ListItem {
  /** The item's own text. */
  content: Inline[]
  /** Anything indented under it -- in practice, a nested list. */
  children: Block[]
}

export type Align = 'left' | 'center' | 'right'

export type Block =
  | { type: 'heading'; level: number; id: string; text: string; children: Inline[] }
  | { type: 'paragraph'; children: Inline[] }
  | { type: 'list'; ordered: boolean; items: ListItem[] }
  | { type: 'code'; value: string }
  | { type: 'table'; align: Align[]; header: Inline[][]; rows: Inline[][][] }
  | { type: 'hr' }

/**
 * GitHub's heading-anchor rule: lower-cased, punctuation dropped, spaces
 * hyphenated. It has to be GitHub's exactly, because the manual's own
 * cross-references (`[Bibliography](#bibliography)`, `#a4-summarising-a-posterior`)
 * were written against a GitHub preview and have to keep resolving in the app.
 */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
}

const ORDERED_ITEM = /^(\s*)(\d+)\.\s+(.*)$/
const BULLET_ITEM = /^(\s*)[-*]\s+(.*)$/
const HEADING = /^(#{1,6})\s+(.*)$/
const FENCE = /^\s*```/
const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/

/**
 * Inline marks, tried in this order so that the ones whose delimiters nest
 * inside others win first: a `**bold**` inside a link is emphasis within the
 * link, but a `*` inside backticks is a multiplication sign.
 *
 * The last alternative autolinks a bare URL, which the bibliography relies on.
 */
const INLINE_SOURCE = [
  '`([^`]+)`', // code
  '\\[([^\\]]+)\\]\\(([^)\\s]+)\\)', // link
  '\\*\\*([^*]+)\\*\\*', // strong
  '\\*([^*]+)\\*', // em
  '(https?://[^\\s)]+)', // bare URL
].join('|')

export function parseInline(source: string): Inline[] {
  const out: Inline[] = []
  let last = 0
  // A fresh matcher per call: this function recurses into a link's or an
  // emphasis's own text, and a shared /g regex would have the inner call move
  // the outer one's lastIndex out from under it.
  const inline = new RegExp(INLINE_SOURCE, 'g')

  let match: RegExpExecArray | null
  while ((match = inline.exec(source)) !== null) {
    if (match.index > last) {
      out.push({ type: 'text', value: source.slice(last, match.index) })
    }
    const [, code, linkText, href, strong, em, url] = match

    if (code !== undefined) {
      out.push({ type: 'code', value: code })
    } else if (linkText !== undefined) {
      out.push({ type: 'link', href, children: parseInline(linkText) })
    } else if (strong !== undefined) {
      out.push({ type: 'strong', children: parseInline(strong) })
    } else if (em !== undefined) {
      out.push({ type: 'em', children: parseInline(em) })
    } else if (url !== undefined) {
      // A URL ending a sentence takes the full stop with it otherwise.
      const trimmed = url.replace(/[.,;:]+$/, '')
      out.push({ type: 'link', href: trimmed, children: [{ type: 'text', value: trimmed }] })
      if (trimmed.length < url.length) {
        out.push({ type: 'text', value: url.slice(trimmed.length) })
      }
    }
    last = match.index + match[0].length
  }

  if (last < source.length) {
    out.push({ type: 'text', value: source.slice(last) })
  }
  return out
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function readAlignment(divider: string): Align[] {
  return splitRow(divider).map((cell) => {
    const left = cell.startsWith(':')
    const right = cell.endsWith(':')
    if (left && right) return 'center'
    if (right) return 'right'
    return 'left'
  })
}

/** The indentation of a list item's marker, used to nest sub-lists. */
function indentOf(line: string): number {
  return /^(\s*)/.exec(line)![1].length
}

function isItem(line: string): boolean {
  return BULLET_ITEM.test(line) || ORDERED_ITEM.test(line)
}

export function parseMarkdown(source: string): Block[] {
  // Hard tabs would make the indentation arithmetic below lie.
  const lines = source.replace(/\t/g, '    ').split('\n')
  return parseBlocks(lines)
}

function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    if (line.trim() === '') {
      i += 1
      continue
    }

    // A fenced block is copied out verbatim: the manual's appendix uses them
    // for equations, where the alignment is the layout.
    if (FENCE.test(line)) {
      const body: string[] = []
      i += 1
      while (i < lines.length && !FENCE.test(lines[i])) {
        body.push(lines[i])
        i += 1
      }
      i += 1 // the closing fence
      blocks.push({ type: 'code', value: body.join('\n') })
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      const text = heading[2].trim()
      blocks.push({
        type: 'heading',
        level: heading[1].length,
        // The id is built from the raw text so that `### A.1 Conventions`
        // and a link to `#a1-conventions` agree; inline marks in a heading
        // (there are none in this manual) would be part of the slug on
        // GitHub too.
        id: slugify(text),
        text,
        children: parseInline(text),
      })
      i += 1
      continue
    }

    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      blocks.push({ type: 'hr' })
      i += 1
      continue
    }

    if (line.trim().startsWith('|') && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1])) {
      const align = readAlignment(lines[i + 1])
      const header = splitRow(line).map(parseInline)
      const rows: Inline[][][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitRow(lines[i]).map(parseInline))
        i += 1
      }
      blocks.push({ type: 'table', align, header, rows })
      continue
    }

    if (isItem(line)) {
      const start = i
      // A list runs to the next blank line. The manual never separates items
      // with one, and treating a blank line as the end keeps a list from
      // swallowing the paragraph that follows it.
      while (i < lines.length && lines[i].trim() !== '') i += 1
      blocks.push(parseList(lines.slice(start, i)))
      continue
    }

    // Anything else is a paragraph, running to the next blank line or to
    // whatever starts a block of its own. The manual is hard-wrapped, so its
    // paragraphs arrive as several lines and are rejoined here.
    const text: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !HEADING.test(lines[i]) &&
      !FENCE.test(lines[i]) &&
      !isItem(lines[i])
    ) {
      text.push(lines[i].trim())
      i += 1
    }
    blocks.push({ type: 'paragraph', children: parseInline(text.join(' ')) })
  }

  return blocks
}

/**
 * One list, possibly holding others.
 *
 * Every line here is either an item at the outermost indentation or belongs
 * to the item above it -- a wrapped continuation line, or a nested list,
 * which is parsed by handing the item's own lines back to parseBlocks with
 * their indentation removed.
 */
function parseList(lines: string[]): Block {
  const base = indentOf(lines[0])
  const ordered = ORDERED_ITEM.test(lines[0])
  const items: ListItem[] = []

  let current: string[] = []
  const flush = () => {
    if (current.length === 0) return
    const first = current[0]
    const marker = ORDERED_ITEM.exec(first) ?? BULLET_ITEM.exec(first)
    const head = marker ? marker[marker.length - 1] : first.trim()

    // Continuation lines wrap the item's own text; anything that starts a
    // block of its own (in practice a nested list) becomes a child block.
    const rest = current.slice(1).map((l) => l.slice(Math.min(indentOf(l), base + 2)))
    const split = rest.findIndex((l) => isItem(l))
    const trailing = split === -1 ? rest : rest.slice(0, split)
    const nested = split === -1 ? [] : rest.slice(split)

    items.push({
      content: parseInline([head, ...trailing.map((l) => l.trim())].join(' ').trim()),
      children: nested.length > 0 ? parseBlocks(nested) : [],
    })
    current = []
  }

  for (const line of lines) {
    if (isItem(line) && indentOf(line) <= base) {
      flush()
    }
    current.push(line)
  }
  flush()

  return { type: 'list', ordered, items }
}

export interface TocEntry {
  id: string
  text: string
  level: number
}

/** The headings a reader can navigate by, in document order. */
export function tableOfContents(blocks: Block[], levels: number[] = [2, 3]): TocEntry[] {
  return blocks
    .filter((b): b is Extract<Block, { type: 'heading' }> => b.type === 'heading')
    .filter((b) => levels.includes(b.level))
    .map(({ id, text, level }) => ({ id, text, level }))
}

/**
 * Drop a section (its heading and everything under it, up to the next heading
 * at the same level or above).
 *
 * The manual carries its own hand-written table of contents, which is what
 * makes it navigable on GitHub. In the app the page builds one from the
 * headings, so the written one would be the same list twice.
 */
export function dropSection(blocks: Block[], id: string): Block[] {
  const start = blocks.findIndex((b) => b.type === 'heading' && b.id === id)
  if (start === -1) return blocks

  const level = (blocks[start] as Extract<Block, { type: 'heading' }>).level
  let end = start + 1
  while (end < blocks.length) {
    const block = blocks[end]
    if (block.type === 'heading' && block.level <= level) break
    end += 1
  }
  return [...blocks.slice(0, start), ...blocks.slice(end)]
}
