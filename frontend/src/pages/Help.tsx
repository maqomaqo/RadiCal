import { useEffect, useMemo, useState } from 'react'
import { Box, Button, Collapse, Container, Link, Paper, Typography, useMediaQuery } from '@mui/material'
import { useTheme } from '@mui/material/styles'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import MenuBookIcon from '@mui/icons-material/MenuBook'
import { Link as RouterLink, useLocation } from 'react-router-dom'
import manualSource from '@/content/USER_MANUAL.md?raw'
import Markdown from '@/components/Markdown'
import { dropSection, parseMarkdown, tableOfContents, type Block } from '@/utils/markdown'
import { DESKTOP, onPageScroll } from '@/desktop/shell'

/**
 * The user manual, in the app.
 *
 * It is the same file the repository holds (src/content/USER_MANUAL.md),
 * parsed and rendered here rather than transcribed into JSX: a manual that
 * has to be edited in two places is a manual that ends up describing two
 * different apps. See utils/markdown.ts for what the parser supports, which
 * is the constraint on how the file may be written.
 *
 * The vocabulary lives next door in the Glossary (pages/Glossary.tsx), which
 * this page links to and which links back. The manual explains what to do;
 * the glossary defines the words it does it in.
 */

/** Everything under one `##` heading, which is one card on the page. */
interface Section {
  id: string
  title: string
  blocks: Block[]
}

function toSections(blocks: Block[]): { title: string; sections: Section[] } {
  let title = 'User Manual'
  const sections: Section[] = []

  for (const block of blocks) {
    if (block.type === 'heading' && block.level === 1) {
      title = block.text
      continue
    }
    if (block.type === 'heading' && block.level === 2) {
      sections.push({ id: block.id, title: block.text, blocks: [block] })
      continue
    }
    // Anything before the first `##` (there is nothing in this manual) would
    // otherwise be dropped, so it opens an untitled section.
    if (sections.length === 0) {
      sections.push({ id: 'introduction', title: '', blocks: [] })
    }
    sections[sections.length - 1].blocks.push(block)
  }

  return { title, sections }
}

export default function Help() {
  const theme = useTheme()
  // The one breakpoint this page turns on: above it the contents list stands
  // beside the manual and tracks the reader; below it, it folds above it.
  const wide = useMediaQuery(theme.breakpoints.up('md'))

  const { title, sections, toc } = useMemo(() => {
    // The written table of contents is dropped in favour of the one built
    // from the headings below, which tracks the reader's position.
    const blocks = dropSection(parseMarkdown(manualSource), 'table-of-contents')
    const parsed = toSections(blocks)
    return { ...parsed, toc: tableOfContents(blocks, [2]) }
  }, [])

  // Which section the reader is in, so the contents list says where they are
  // in a document this long. Only tracked where the list is beside the manual
  // to be marked up (see `wide`).
  const [activeId, setActiveId] = useState<string>(toc[0]?.id ?? '')

  // A link from elsewhere in the app to one section of the manual -- the
  // Results panels' "Reading a wiggle match" -- arrives as a client-side
  // navigation, which does not scroll to its hash the way a page load would.
  const { hash } = useLocation()
  useEffect(() => {
    if (!hash) return
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView()
  }, [hash])

  // Fifteen sections is a screenful and a half on a phone, where the contents
  // list sits above the manual rather than beside it -- open, it would be the
  // whole first screen. On `md` and up it is always shown and this is unused.
  const [contentsOpen, setContentsOpen] = useState(false)

  useEffect(() => {
    if (!wide) return

    // The topmost heading that has passed under the AppBar is the one being
    // read. A scroll listener rather than an IntersectionObserver: the answer
    // depends on the ORDER of the headings that have passed, which an
    // observer reports one crossing at a time, and reading it off positions
    // is both shorter and exact at every scroll position rather than only at
    // the ones where something crossed.
    let frame = 0
    const update = () => {
      frame = 0
      const headings = toc
        .map((entry) => document.getElementById(entry.id))
        .filter((el): el is HTMLElement => el !== null)
      if (headings.length === 0) return
      const passed = headings.filter((el) => el.getBoundingClientRect().top <= 96)
      setActiveId((passed[passed.length - 1] ?? headings[0]).id)
    }
    const onScroll = () => {
      // One measurement per frame: getBoundingClientRect on every heading is
      // a layout read, and scroll events outnumber frames.
      if (frame === 0) frame = requestAnimationFrame(update)
    }

    update()
    const stopScroll = onPageScroll(onScroll)
    window.addEventListener('resize', onScroll)
    return () => {
      if (frame !== 0) cancelAnimationFrame(frame)
      stopScroll()
      window.removeEventListener('resize', onScroll)
    }
  }, [toc, wide])

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Paper sx={{ p: 3, mb: 3 }}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            gap: 2,
            flexWrap: 'wrap',
          }}
        >
          <Box sx={{ flexGrow: 1, minWidth: 260 }}>
            <Typography variant="h1" component="h1">
              {title}
            </Typography>
            <Typography color="text.secondary" sx={{ mt: 1.5, maxWidth: '68ch' }}>
              How to use RadiCal, from a first calibration to the mathematics behind every number it
              reports. For the terms themselves — BP, HPD, ΔR, agreement index — see the{' '}
              <Link component={RouterLink} to="/help/glossary">
                Glossary
              </Link>
              .
            </Typography>
          </Box>
          <Button
            component={RouterLink}
            to="/help/glossary"
            variant="outlined"
            startIcon={<MenuBookIcon />}
            sx={{ flexShrink: 0 }}
          >
            Glossary
          </Button>
        </Box>
      </Paper>

      <Box
        sx={{
          display: 'grid',
          // The contents list sits beside the manual where there is room for
          // it, and above the manual where there is not.
          // `minmax(0, 1fr)` rather than `1fr`: the appendix's equation
          // blocks and the wider tables have a min-content width larger than
          // a phone, and a bare `1fr` track grows to it, taking the whole
          // page sideways with it instead of letting them scroll themselves.
          gridTemplateColumns: { xs: 'minmax(0, 1fr)', md: '240px minmax(0, 1fr)' },
          alignItems: 'start',
          gap: 3,
        }}
      >
        <Paper
          component="nav"
          aria-label="Manual contents"
          sx={{
            p: 2,
            // Long enough that scrolling back to the top to move between
            // sections would be its own chore.
            position: { md: 'sticky' },
            top: { md: DESKTOP ? 24 : 88 },
            maxHeight: { md: 'calc(100vh - 112px)' },
            overflowY: { md: 'auto' },
          }}
        >
          <Button
            onClick={() => setContentsOpen((open) => !open)}
            aria-expanded={contentsOpen}
            endIcon={
              <ExpandMoreIcon
                sx={{
                  transition: 'transform 150ms',
                  transform: contentsOpen ? 'rotate(180deg)' : 'none',
                }}
              />
            }
            sx={{
              display: { xs: 'flex', md: 'none' },
              width: '100%',
              justifyContent: 'space-between',
              color: 'text.secondary',
              typography: 'overline',
            }}
          >
            Contents
          </Button>
          <Typography
            variant="overline"
            color="text.secondary"
            sx={{ display: { xs: 'none', md: 'block' }, lineHeight: 2 }}
          >
            Contents
          </Typography>
          <Collapse in={contentsOpen || wide}>
            <Box component="ol" sx={{ listStyle: 'none', m: 0, p: 0 }}>
              {toc.map((entry) => (
                <Box component="li" key={entry.id}>
                  <Link
                    href={`#${entry.id}`}
                    // Choosing a section on a phone is choosing where to read;
                    // the list has done its job and folds away again.
                    onClick={() => setContentsOpen(false)}
                    aria-current={wide && activeId === entry.id ? 'true' : undefined}
                    sx={{
                      display: 'block',
                      py: 0.75,
                      pl: 1,
                      fontSize: '0.875rem',
                      lineHeight: 1.35,
                      textDecoration: 'none',
                      color: wide && activeId === entry.id ? 'primary.main' : 'text.secondary',
                      fontWeight: wide && activeId === entry.id ? 600 : 400,
                      // The mark for the section being read, drawn on the side
                      // the list is read from. Only where the list is beside
                      // the manual: folded above it, nothing is "current".
                      borderLeft: 2,
                      borderColor: wide && activeId === entry.id ? 'primary.main' : 'transparent',
                      '&:hover': { color: 'primary.main', textDecoration: 'underline' },
                    }}
                  >
                    {entry.text}
                  </Link>
                </Box>
              ))}
            </Box>
          </Collapse>
        </Paper>

        <Box sx={{ minWidth: 0 }}>
          {sections.map((section) => (
            <Paper key={section.id} sx={{ p: 3, mb: 3 }}>
              <Markdown blocks={section.blocks} />
            </Paper>
          ))}

          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography sx={{ fontSize: '1.1875rem', fontWeight: 600 }}>
              Still looking for a word?
            </Typography>
            <Typography color="text.secondary" sx={{ mt: 1, maxWidth: '68ch' }}>
              The Glossary defines the vocabulary this manual is written in, from radiocarbon age
              through to the diagnostics on a finished model.
            </Typography>
            <Button
              component={RouterLink}
              to="/help/glossary"
              variant="outlined"
              startIcon={<MenuBookIcon />}
              sx={{ mt: 2 }}
            >
              Open the Glossary
            </Button>
          </Paper>
        </Box>
      </Box>
    </Container>
  )
}
