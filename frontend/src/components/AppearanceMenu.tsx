import { useState } from 'react'
import { IconButton, ListItemIcon, ListItemText, ListSubheader, Menu, MenuItem, Tooltip } from '@mui/material'
import CheckIcon from '@mui/icons-material/Check'
import DarkModeOutlinedIcon from '@mui/icons-material/DarkModeOutlined'
import LightModeOutlinedIcon from '@mui/icons-material/LightModeOutlined'
import SettingsBrightnessOutlinedIcon from '@mui/icons-material/SettingsBrightnessOutlined'
import { useColorMode, type ColorModePreference } from '@/contexts/ColorModeContext'

const OPTIONS: Array<{ value: ColorModePreference; label: string; icon: typeof CheckIcon }> = [
  { value: 'system', label: 'Match system', icon: SettingsBrightnessOutlinedIcon },
  { value: 'light', label: 'Light', icon: LightModeOutlinedIcon },
  { value: 'dark', label: 'Dark', icon: DarkModeOutlinedIcon },
]

const ICONS: Record<ColorModePreference, typeof CheckIcon> = {
  system: SettingsBrightnessOutlinedIcon,
  light: LightModeOutlinedIcon,
  dark: DarkModeOutlinedIcon,
}

/**
 * The three choices as menu rows, for whichever menu holds them. An array and
 * not a fragment, because MUI's Menu does not accept a Fragment as a child.
 * The chosen row carries a check as well as the selected wash: a wash is
 * colour alone, and on the bar's menu it is also the only thing that moves.
 */
export function appearanceItems(onChosen?: () => void) {
  return [
    <ListSubheader key="heading" sx={{ lineHeight: '32px', background: 'transparent' }}>
      Appearance
    </ListSubheader>,
    ...OPTIONS.map((option) => <AppearanceItem key={option.value} option={option} onChosen={onChosen} />),
  ]
}

function AppearanceItem({
  option,
  onChosen,
}: {
  option: (typeof OPTIONS)[number]
  onChosen?: () => void
}) {
  const { preference, setPreference } = useColorMode()
  const chosen = preference === option.value
  const Icon = option.icon
  return (
    <MenuItem
      role="menuitemradio"
      aria-checked={chosen}
      selected={chosen}
      onClick={() => {
        setPreference(option.value)
        onChosen?.()
      }}
    >
      <ListItemIcon>
        <Icon fontSize="small" />
      </ListItemIcon>
      <ListItemText>{option.label}</ListItemText>
      {chosen && <CheckIcon fontSize="small" sx={{ ml: 2 }} />}
    </MenuItem>
  )
}

/**
 * The bar's control for light, dark, or whichever the system is in. Its icon
 * is the choice, not the result: "Match system" shows the system glyph even
 * while the screen is dark, so the control says what it will keep doing.
 */
export default function AppearanceMenu() {
  const { preference } = useColorMode()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const Icon = ICONS[preference]
  return (
    <>
      <Tooltip title="Appearance">
        <IconButton
          color="inherit"
          aria-label="Appearance"
          aria-haspopup="menu"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{
            display: { xs: 'none', sm: 'inline-flex' },
            p: 1.25,
            flexShrink: 0,
            '&:hover': { backgroundColor: 'rgba(255,255,255,0.1)' },
          }}
        >
          <Icon />
        </IconButton>
      </Tooltip>
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        MenuListProps={{ 'aria-label': 'Appearance' }}
      >
        {appearanceItems(() => setAnchor(null))}
      </Menu>
    </>
  )
}
