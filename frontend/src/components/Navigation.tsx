import { useState } from 'react'
import { AppBar, Toolbar, Typography, Box, Button, Container, Divider, IconButton, Menu, MenuItem, Tooltip } from '@mui/material'
import MenuIcon from '@mui/icons-material/Menu'
import HelpOutlineIcon from '@mui/icons-material/HelpOutline'
import LogoutIcon from '@mui/icons-material/Logout'
import ManageAccountsIcon from '@mui/icons-material/ManageAccounts'
import PersonOutlineIcon from '@mui/icons-material/PersonOutline'
import { Link as RouterLink, useLocation } from 'react-router-dom'
import { useAuth } from '@/contexts/AuthContext'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/projects', label: 'Projects' },
  { to: '/curves', label: 'Curves' },
]

/**
 * Help is not one of the app's places, it is the way out of being stuck in
 * any of them, so it is a question mark on the controls side of the bar
 * rather than a fourth tab in the section rail. It joins the sections in the
 * small-screen menu, where there is no room for the distinction.
 */
const HELP_LINK = { to: '/help', label: 'Help' }

/**
 * A section is "where you are" for every page beneath it, not just its index:
 * /projects/12/compare is still Projects. Dashboard is the exception because
 * its path is a prefix of everything.
 */
function isCurrentSection(pathname: string, to: string) {
  return to === '/' ? pathname === '/' : pathname === to || pathname.startsWith(`${to}/`)
}

export default function Navigation() {
  const { user, signOut } = useAuth()
  const { pathname } = useLocation()

  // The bar reads left to right as three things, not one list of six: who
  // the app is and where you are in it, then space that belongs to nobody,
  // then the controls -- support, account, and the one action the bar exists
  // to offer. The gap between the two halves is the grouping; it is why the
  // sections sit beside the wordmark rather than adrift beside New Analysis.
  //
  // Two menus, because they answer different questions and are needed at
  // different widths:
  const [navAnchor, setNavAnchor] = useState<HTMLElement | null>(null)
  const [accountAnchor, setAccountAnchor] = useState<HTMLElement | null>(null)

  return (
    <AppBar position="sticky">
      <Container maxWidth="lg">
        <Toolbar
          disableGutters
          sx={{
            gap: 1,
            // The theme's focus ring is the primary blue, which is also this
            // bar's background -- so every control up here focused into
            // nothing. Inset rather than offset because a full-height section
            // tab's ring would otherwise be clipped by the bar's own edges.
            '& :focus-visible, & .MuiButtonBase-root.Mui-focusVisible': {
              outline: '2px solid #fff',
              outlineOffset: '-2px',
            },
          }}
        >
          <IconButton
            color="inherit"
            aria-label="Open sections menu"
            aria-haspopup="menu"
            onClick={(e) => setNavAnchor(e.currentTarget)}
            sx={{
              display: { xs: 'inline-flex', sm: 'none' },
              // 40px is MUI's default and under every touch-target
              // guideline going; this is the only control on the bar at
              // this width besides the primary action.
              p: 1.25,
              ml: -1,
            }}
          >
            <MenuIcon />
          </IconButton>
          <Menu anchorEl={navAnchor} open={!!navAnchor} onClose={() => setNavAnchor(null)}>
            {[...NAV_LINKS, HELP_LINK].map((link) => (
              <MenuItem
                key={link.to}
                component={RouterLink}
                to={link.to}
                selected={isCurrentSection(pathname, link.to)}
                onClick={() => setNavAnchor(null)}
              >
                {link.label}
              </MenuItem>
            ))}
          </Menu>

          <Typography
            variant="h6"
            component={RouterLink}
            to="/"
            sx={{
              // Deliberately not flexGrow: the flexible space is the Box
              // below, outside the anchor. Growing the wordmark made the
              // link 512px wide at desktop width, so a click on the empty
              // middle of the bar -- the least likely place anyone means to
              // click anything -- navigated home.
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              textDecoration: 'none',
              color: 'inherit',
              fontWeight: 600,
              whiteSpace: 'nowrap',
            }}
          >
            {/* Decorative -- "RadiCal" right after it already names the
                link, so an alt here would just repeat it for a screen
                reader. The 72px copy is the 36px box at 2x; the source
                logo (public/RadiCal.png) is 4267px square and 400 kB, which
                every page was downloading to draw an icon. */}
            <Box component="img" src="/RadiCal-72.png" alt="" width={36} height={36} sx={{ width: 36, height: 36 }} />
            RadiCal
          </Typography>

          {/* The section rail. Full toolbar height and square, so the
              current section can be marked by a rule on the bar's own edge
              the way a tab strip marks one -- an indicator floating under a
              pill would belong to nothing. Tight spacing inside the group,
              because these three are one thing; the flexible gap after it
              is what separates them from the controls.

              ml: 4 rather than the Toolbar's own gap: the wordmark is the
              app's name, not a fourth section, and 32px says so. */}
          <Box
            component="nav"
            aria-label="Sections"
            sx={{
              display: { xs: 'none', sm: 'flex' },
              alignSelf: 'stretch',
              alignItems: 'stretch',
              gap: 0.5,
              ml: { sm: 2, md: 4 },
            }}
          >
            {NAV_LINKS.map((link) => {
              const current = isCurrentSection(pathname, link.to)
              return (
                <Button
                  key={link.to}
                  color="inherit"
                  component={RouterLink}
                  to={link.to}
                  aria-current={current ? 'page' : undefined}
                  sx={{
                    borderRadius: 0,
                    px: 1.5,
                    flexShrink: 0,
                    fontWeight: current ? 600 : 400,
                    // Not a dimmed inactive label: white at any alpha over
                    // this blue drops under 4.5:1, and these are the app's
                    // primary navigation. Weight and the rule carry the
                    // state instead, and every label stays at full contrast.
                    '&::after': {
                      content: '""',
                      position: 'absolute',
                      // The full width of the tab, so the rule and the
                      // hover block describe the same rectangle.
                      left: 0,
                      right: 0,
                      bottom: 0,
                      height: 2,
                      backgroundColor: 'currentColor',
                      opacity: current ? 1 : 0,
                    },
                    '&:hover': {
                      backgroundColor: 'rgba(255,255,255,0.1)',
                    },
                    '&:hover::after': { opacity: current ? 1 : 0.4 },
                  }}
                >
                  {link.label}
                </Button>
              )
            })}
          </Box>

          {/* Space that belongs to nobody and is clickable by nobody. */}
          <Box sx={{ flexGrow: 1 }} />

          <Tooltip title="Help">
            <IconButton
              color="inherit"
              component={RouterLink}
              to={HELP_LINK.to}
              // The icon is the whole label, so it carries one of its own.
              aria-label="Help"
              sx={{
                display: { xs: 'none', sm: 'inline-flex' },
                p: 1.25,
                flexShrink: 0,
                '&:hover': { backgroundColor: 'rgba(255,255,255,0.1)' },
              }}
            >
              <HelpOutlineIcon />
            </IconButton>
          </Tooltip>

          {/* Who you are, and the way out. The address rather than an avatar
              or initials: on an installation where everything is private per
              account, "which account am I looking at this through" is a
              question with a real answer, and initials do not give it.

              It is a menu rather than a bare sign-out button for two
              reasons. The address is long and the bar is not, so the trigger
              is allowed to truncate only because the menu spells the whole
              thing out underneath; a truncated address answers the question
              no better than initials would. And below `md` the trigger drops
              to its icon, which is what makes the account reachable at every
              width -- the previous arrangement hid the button below `md` and
              the burger above `sm`, leaving 600-899px with no identity and
              no way to sign out at all. */}
          <Tooltip title={`Signed in as ${user?.display_name || user?.email || ''}`}>
            <Button
              color="inherit"
              onClick={(e) => setAccountAnchor(e.currentTarget)}
              aria-label="Account"
              aria-haspopup="menu"
              startIcon={<PersonOutlineIcon />}
              sx={{
                flexShrink: 0,
                maxWidth: 220,
                minWidth: 0,
                px: { xs: 1.25, md: 1.5 },
                '& .MuiButton-startIcon': { mr: { xs: 0, md: 0.5 }, ml: 0 },
                '&:hover': { backgroundColor: 'rgba(255,255,255,0.1)' },
              }}
            >
              <Box
                component="span"
                sx={{
                  display: { xs: 'none', md: 'block' },
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {user?.display_name || user?.email}
              </Box>
            </Button>
          </Tooltip>
          <Menu
            anchorEl={accountAnchor}
            open={!!accountAnchor}
            onClose={() => setAccountAnchor(null)}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
            transformOrigin={{ vertical: 'top', horizontal: 'right' }}
          >
            {/* A heading, not a MenuItem: it is the answer to "which
                account", not a thing to choose. Unwrapped and untruncated,
                which is the whole reason the trigger is allowed to shorten
                it. */}
            <Box sx={{ px: 2, pt: 0.5, pb: 1.5, maxWidth: 320 }}>
              <Typography variant="caption" color="text.secondary" component="div">
                Signed in as
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600, wordBreak: 'break-all' }}>
                {user?.email}
              </Typography>
              {user?.display_name && (
                <Typography variant="body2" color="text.secondary">
                  {user.display_name}
                </Typography>
              )}
            </Box>
            <Divider />
            <MenuItem
              component={RouterLink}
              to="/account"
              onClick={() => setAccountAnchor(null)}
              sx={{ mt: 0.5, gap: 1.5 }}
            >
              <ManageAccountsIcon fontSize="small" />
              Account and access tokens
            </MenuItem>
            <MenuItem onClick={() => { setAccountAnchor(null); signOut() }} sx={{ gap: 1.5 }}>
              <LogoutIcon fontSize="small" />
              Sign out
            </MenuItem>
          </Menu>

          <Button
            color="inherit"
            component={RouterLink}
            to="/new-analysis"
            // "New Analysis" means start over, so it is one of the few places
            // that says so: NewAnalysis.tsx keeps whatever draft is there
            // unless an arrival is marked fresh.
            state={{ fresh: true }}
            variant="outlined"
            sx={{
              borderColor: 'rgba(255,255,255,0.5)',
              flexShrink: 0,
              // The one thing on this bar that is an action rather than
              // chrome, so it is held apart from the chrome it sits beside.
              ml: 2,
              '&:hover': {
                borderColor: 'white',
                backgroundColor: 'rgba(255,255,255,0.1)',
              },
            }}
          >
            New Analysis
          </Button>
        </Toolbar>
      </Container>
    </AppBar>
  )
}
