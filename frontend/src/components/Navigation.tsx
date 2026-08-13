import { useState } from 'react'
import { AppBar, Toolbar, Typography, Box, Button, Container, IconButton, Menu, MenuItem } from '@mui/material'
import MenuIcon from '@mui/icons-material/Menu'
import { Link as RouterLink } from 'react-router-dom'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard' },
  { to: '/projects', label: 'Projects' },
]

export default function Navigation() {
  // Three buttons at full width (Dashboard, Projects, New Analysis) don't
  // fit beside the wordmark below `sm` -- the row used to overflow the
  // viewport rather than wrap, since a sticky AppBar wrapping to a second
  // line jumps the whole page's content down by a row on every load. Below
  // `sm` the links move into this menu instead; New Analysis stays visible
  // as it's the primary action.
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)

  return (
    <AppBar position="sticky">
      <Container maxWidth="lg">
        <Toolbar disableGutters>
          <Typography
            variant="h6"
            component={RouterLink}
            to="/"
            sx={{
              flexGrow: 1,
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              textDecoration: 'none',
              color: 'inherit',
              fontWeight: 600,
            }}
          >
            {/* Decorative -- "RadiCal" right after it already names the
                link, so an alt here would just repeat it for a screen
                reader. */}
            <Box component="img" src="/RadiCal.png" alt="" sx={{ width: 28, height: 28 }} />
            RadiCal
          </Typography>

          <Box sx={{ display: { xs: 'none', sm: 'flex' }, gap: 2, alignItems: 'center' }}>
            {NAV_LINKS.map((link) => (
              <Button
                key={link.to}
                color="inherit"
                component={RouterLink}
                to={link.to}
                sx={{
                  '&:hover': {
                    backgroundColor: 'rgba(255,255,255,0.1)',
                  },
                }}
              >
                {link.label}
              </Button>
            ))}
          </Box>

          <IconButton
            color="inherit"
            aria-label="Navigation menu"
            onClick={(e) => setAnchorEl(e.currentTarget)}
            sx={{ display: { xs: 'inline-flex', sm: 'none' }, mr: 1 }}
          >
            <MenuIcon />
          </IconButton>
          <Menu anchorEl={anchorEl} open={!!anchorEl} onClose={() => setAnchorEl(null)}>
            {NAV_LINKS.map((link) => (
              <MenuItem
                key={link.to}
                component={RouterLink}
                to={link.to}
                onClick={() => setAnchorEl(null)}
              >
                {link.label}
              </MenuItem>
            ))}
          </Menu>

          <Button
            color="inherit"
            component={RouterLink}
            to="/new-analysis"
            variant="outlined"
            sx={{
              borderColor: 'rgba(255,255,255,0.5)',
              flexShrink: 0,
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
