import {
  Box,
  Link,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import type { Block, Inline } from '@/utils/markdown'
import { DESKTOP } from '@/desktop/shell'

/**
 * The rendering half of utils/markdown.ts: parsed blocks in, MUI out.
 *
 * Everything here is theme tokens rather than fixed colours or sizes, so the
 * manual reads as part of the app rather than as a document embedded in it --
 * its tables are the app's tables, its links the app's links.
 */

/** Prose, at a readable measure rather than the full container width. */
const MEASURE = '72ch'

function renderInline(nodes: Inline[], keyPrefix = ''): React.ReactNode {
  return nodes.map((node, index) => {
    const key = `${keyPrefix}${index}`
    switch (node.type) {
      case 'text':
        return <span key={key}>{node.value}</span>
      case 'strong':
        return (
          <Box key={key} component="strong" sx={{ fontWeight: 600, color: 'text.primary' }}>
            {renderInline(node.children, `${key}-`)}
          </Box>
        )
      case 'em':
        return (
          <em key={key}>{renderInline(node.children, `${key}-`)}</em>
        )
      case 'code':
        return (
          <Box
            key={key}
            component="code"
            sx={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              fontSize: '0.875em',
              backgroundColor: 'background.muted',
              borderRadius: 1,
              px: 0.6,
              py: 0.15,
              // A long identifier should break rather than push the column
              // wider than the page.
              wordBreak: 'break-word',
            }}
          >
            {node.value}
          </Box>
        )
      case 'link': {
        // A link into the document itself is a jump, not a departure: it stays
        // in this tab and needs no new-window affordance.
        const internal = node.href.startsWith('#') || node.href.startsWith('/')
        return (
          <Link
            key={key}
            href={node.href}
            {...(internal ? {} : { target: '_blank', rel: 'noopener noreferrer' })}
          >
            {renderInline(node.children, `${key}-`)}
          </Link>
        )
      }
    }
  })
}

/** The heading sizes, which are the page's own scale rather than MUI's h1-h6. */
const HEADING_SX: Record<number, object> = {
  1: { fontSize: '2.25rem', fontWeight: 600, mt: 0 },
  2: { fontSize: '1.5rem', fontWeight: 600, mt: 0 },
  3: { fontSize: '1.1875rem', fontWeight: 600, mt: 4 },
  4: { fontSize: '1.0625rem', fontWeight: 600, mt: 3 },
}

function renderBlock(block: Block, key: string): React.ReactNode {
  switch (block.type) {
    case 'heading':
      return (
        <Typography
          key={key}
          id={block.id}
          component={`h${Math.min(block.level, 6)}` as 'h2'}
          sx={{
            ...(HEADING_SX[block.level] ?? HEADING_SX[4]),
            mb: 1,
            // Clears the sticky AppBar when a link jumps to this heading.
            scrollMarginTop: DESKTOP ? 24 : 88,
          }}
        >
          {renderInline(block.children, `${key}-`)}
        </Typography>
      )

    case 'paragraph':
      return (
        <Typography key={key} color="text.secondary" sx={{ mt: 2, maxWidth: MEASURE }}>
          {renderInline(block.children, `${key}-`)}
        </Typography>
      )

    case 'list':
      return (
        <Box
          key={key}
          component={block.ordered ? 'ol' : 'ul'}
          sx={{
            mt: 1.5,
            mb: 0,
            pl: 3,
            maxWidth: MEASURE,
            color: 'text.secondary',
            '& li': { mt: 1 },
            // A nested list is a level of detail under its parent, not a new
            // list: no extra gap above it.
            '& ul, & ol': { mt: 0.5, pl: 3 },
          }}
        >
          {block.items.map((item, index) => (
            <Box component="li" key={`${key}-${index}`}>
              {renderInline(item.content, `${key}-${index}-`)}
              {item.children.map((child, childIndex) =>
                renderBlock(child, `${key}-${index}-${childIndex}`)
              )}
            </Box>
          ))}
        </Box>
      )

    case 'code':
      return (
        <Box
          key={key}
          component="pre"
          sx={{
            mt: 2,
            mb: 0,
            p: 2,
            backgroundColor: 'background.muted',
            borderRadius: 1,
            border: 1,
            borderColor: 'divider',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            fontSize: '0.8125rem',
            lineHeight: 1.6,
            color: 'text.primary',
            // The appendix's equations are laid out by their own whitespace,
            // so they scroll rather than wrap.
            overflowX: 'auto',
          }}
        >
          {block.value}
        </Box>
      )

    case 'table':
      return (
        <TableContainer key={key} sx={{ mt: 2, border: 1, borderColor: 'divider', borderRadius: 1 }}>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ backgroundColor: 'background.muted' }}>
                {block.header.map((cell, index) => (
                  <TableCell
                    key={`${key}-h${index}`}
                    align={block.align[index] ?? 'left'}
                    sx={{ fontWeight: 600 }}
                  >
                    {renderInline(cell, `${key}-h${index}-`)}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {block.rows.map((row, rowIndex) => (
                <TableRow key={`${key}-r${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <TableCell
                      key={`${key}-r${rowIndex}-${cellIndex}`}
                      align={block.align[cellIndex] ?? 'left'}
                      sx={{ color: 'text.secondary' }}
                    >
                      {renderInline(cell, `${key}-r${rowIndex}-${cellIndex}-`)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )

    case 'hr':
      return <Box key={key} component="hr" sx={{ mt: 3, border: 0, borderTop: 1, borderColor: 'divider' }} />
  }
}

export default function Markdown({ blocks }: { blocks: Block[] }) {
  return <>{blocks.map((block, index) => renderBlock(block, `b${index}`))}</>
}
