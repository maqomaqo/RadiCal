import { describe, expect, it } from 'vitest'
import manual from '@/content/USER_MANUAL.md?raw'
import {
  dropSection,
  parseInline,
  parseMarkdown,
  slugify,
  tableOfContents,
  type Block,
  type Inline,
} from './markdown'

/**
 * The manual is rendered by this parser rather than transcribed into JSX, so
 * what it supports is what the manual is allowed to use. These assertions are
 * the contract: each one is a construct docs the user manual actually contains.
 */

function only<T extends Block['type']>(source: string, type: T) {
  const blocks = parseMarkdown(source)
  expect(blocks).toHaveLength(1)
  expect(blocks[0].type).toBe(type)
  return blocks[0] as Extract<Block, { type: T }>
}

describe('slugify', () => {
  // The manual's own cross-references were written against GitHub's anchors.
  it('matches the anchors the manual links to', () => {
    expect(slugify('Model Structure (Bayesian only)')).toBe('model-structure-bayesian-only')
    expect(slugify('A.4 Summarising a posterior')).toBe('a4-summarising-a-posterior')
    expect(slugify('Appendix A: Mathematical Methods')).toBe('appendix-a-mathematical-methods')
  })
})

describe('parseInline', () => {
  it('reads code, emphasis and links', () => {
    expect(parseInline('a `code` b **bold** c *italic* d')).toEqual([
      { type: 'text', value: 'a ' },
      { type: 'code', value: 'code' },
      { type: 'text', value: ' b ' },
      { type: 'strong', children: [{ type: 'text', value: 'bold' }] },
      { type: 'text', value: ' c ' },
      { type: 'em', children: [{ type: 'text', value: 'italic' }] },
      { type: 'text', value: ' d' },
    ])
  })

  it('keeps an underscore in an identifier', () => {
    // `sample_name` and `age_bp` are column names in the import section, and
    // underscore emphasis would eat the middle of them.
    expect(parseInline('sample_name and age_bp')).toEqual([
      { type: 'text', value: 'sample_name and age_bp' },
    ])
  })

  it('does not mark up inside code', () => {
    expect(parseInline('`a * b`')).toEqual([{ type: 'code', value: 'a * b' }])
  })

  it('autolinks a bare URL without its trailing full stop', () => {
    expect(parseInline('see https://example.org/x.')).toEqual([
      { type: 'text', value: 'see ' },
      {
        type: 'link',
        href: 'https://example.org/x',
        children: [{ type: 'text', value: 'https://example.org/x' }],
      },
      { type: 'text', value: '.' },
    ])
  })
})

describe('parseMarkdown', () => {
  it('rejoins a hard-wrapped paragraph', () => {
    const block = only('one line\nand its continuation\n', 'paragraph')
    expect(block.children).toEqual([{ type: 'text', value: 'one line and its continuation' }])
  })

  it('gives a heading an id and stops the paragraph before it', () => {
    const blocks = parseMarkdown('text\n\n## A Heading\nmore text\n')
    expect(blocks.map((b) => b.type)).toEqual(['paragraph', 'heading', 'paragraph'])
    expect(blocks[1]).toMatchObject({ level: 2, id: 'a-heading', text: 'A Heading' })
  })

  it('nests a sub-list under the item it is indented beneath', () => {
    const block = only('- outer\n  - inner one\n  - inner two\n', 'list')
    expect(block.ordered).toBe(false)
    expect(block.items).toHaveLength(1)
    const nested = block.items[0].children
    expect(nested).toHaveLength(1)
    expect(nested[0]).toMatchObject({ type: 'list' })
    expect((nested[0] as Extract<Block, { type: 'list' }>).items).toHaveLength(2)
  })

  it('joins an item with its wrapped continuation', () => {
    const block = only('- an item that\n  wraps\n- a second\n', 'list')
    expect(block.items).toHaveLength(2)
    expect(block.items[0].content).toEqual([{ type: 'text', value: 'an item that wraps' }])
  })

  it('ends a list at the blank line, not at the paragraph after it', () => {
    const blocks = parseMarkdown('- one\n- two\n\nA paragraph.\n')
    expect(blocks.map((b) => b.type)).toEqual(['list', 'paragraph'])
  })

  it('reads an ordered list', () => {
    const block = only('1. first\n2. second\n', 'list')
    expect(block.ordered).toBe(true)
    expect(block.items).toHaveLength(2)
  })

  it('keeps a fenced block verbatim', () => {
    const block = only('```\n  sigma^2 = a + b\n\n  * w(t)\n```\n', 'code')
    expect(block.value).toBe('  sigma^2 = a + b\n\n  * w(t)')
  })

  it('reads a table with its alignment', () => {
    const block = only('| Range | Why |\n| --- | ---: |\n| a | b |\n| c | d |\n', 'table')
    expect(block.align).toEqual(['left', 'right'])
    expect(block.header[0]).toEqual([{ type: 'text', value: 'Range' }])
    expect(block.rows).toHaveLength(2)
    expect(block.rows[1][1]).toEqual([{ type: 'text', value: 'd' }])
  })
})

describe('page navigation', () => {
  const source = [
    '# Manual',
    '',
    '## Table of Contents',
    '',
    '1. [One](#one)',
    '',
    '## One',
    '',
    'Body.',
    '',
    '### Detail',
    '',
    '## Two',
    '',
  ].join('\n')

  it('lists the headings at the requested levels', () => {
    expect(tableOfContents(parseMarkdown(source), [2])).toEqual([
      { id: 'table-of-contents', text: 'Table of Contents', level: 2 },
      { id: 'one', text: 'One', level: 2 },
      { id: 'two', text: 'Two', level: 2 },
    ])
  })

  it('drops a section and everything under it, up to the next of its level', () => {
    const blocks = dropSection(parseMarkdown(source), 'table-of-contents')
    expect(tableOfContents(blocks, [2]).map((entry) => entry.id)).toEqual(['one', 'two'])
    // The section under the one that was dropped is untouched.
    expect(tableOfContents(blocks, [3]).map((entry) => entry.id)).toEqual(['detail'])
  })
})

describe('the manual itself', () => {
  const blocks = parseMarkdown(manual)

  function walk(nodes: Inline[], visit: (node: Inline) => void) {
    for (const node of nodes) {
      visit(node)
      if (node.type === 'strong' || node.type === 'em' || node.type === 'link') {
        walk(node.children, visit)
      }
    }
  }

  function everyInline(): Inline[] {
    const found: Inline[] = []
    const collect = (nodes: Inline[]) => walk(nodes, (node) => found.push(node))
    const fromBlock = (block: Block) => {
      switch (block.type) {
        case 'heading':
        case 'paragraph':
          collect(block.children)
          break
        case 'list':
          block.items.forEach((item) => {
            collect(item.content)
            item.children.forEach(fromBlock)
          })
          break
        case 'table':
          block.header.forEach(collect)
          block.rows.forEach((row) => row.forEach(collect))
          break
        default:
          break
      }
    }
    blocks.forEach(fromBlock)
    return found
  }

  it('has a unique id for every heading', () => {
    const ids = blocks
      .filter((b): b is Extract<Block, { type: 'heading' }> => b.type === 'heading')
      .map((b) => b.id)
    expect(ids.length).toBeGreaterThan(20)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('resolves every one of its own cross-references', () => {
    const ids = new Set(
      blocks.filter((b) => b.type === 'heading').map((b) => (b as { id: string }).id)
    )
    const broken = everyInline()
      .filter((node): node is Extract<Inline, { type: 'link' }> => node.type === 'link')
      .map((node) => node.href)
      .filter((href) => href.startsWith('#') && !ids.has(href.slice(1)))
    expect(broken).toEqual([])
  })

  it('leaves no markup unparsed', () => {
    // Syntax this parser does not implement would otherwise reach the page as
    // literal asterisks and brackets, which is the failure mode worth
    // catching: the manual is edited as Markdown by people who cannot see
    // this file.
    const leftovers = everyInline()
      .filter((node): node is Extract<Inline, { type: 'text' }> => node.type === 'text')
      .map((node) => node.value)
      .filter((value) => /\*\*|\]\(|^\s*[-*]\s|`/.test(value))
    expect(leftovers).toEqual([])
  })
})
