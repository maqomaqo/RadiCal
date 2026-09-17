// A spatial order, offered for the analyst to accept or reject.
//
// THE ONE LINE THIS DIALOG EXISTS TO SAY: a spanning tree is spatial
// adjacency, not chronology. Accepting this order asserts that everything at
// one site predates everything at the next — a strong claim the dates may
// refute, and nothing about the sites' positions makes it true.
//
// That is also exactly its value, and the dialog says so too: accepting makes
// the claim TESTABLE. Run the model, read the agreement indices, and the sites
// that do not fit the spatial order announce themselves. A proposal nobody can
// disagree with would not be worth offering.
//
// So this is never applied automatically, and the accept button says "Apply
// this order" rather than "OK".
import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography,
} from '@mui/material'

import SiteMap from '@/components/SiteMap'
import { proposeSpatialSequence } from '@/api/dispersal'
import type { SequenceProposal } from '@/api/dispersal'
import { proposalChangesAnything } from '@/utils/spatialOrder'
import type { MatrixMeasurement, ModelElement } from '@/types'

interface Props {
  open: boolean
  tree: ModelElement
  measurements: MatrixMeasurement[]
  onClose: () => void
  /** Called with the proposed element ids, in order, only if the user accepts. */
  onApply: (orderedIds: string[]) => void
}

export default function SpatialOrderDialog({
  open, tree, measurements, onClose, onApply,
}: Props) {
  const [proposal, setProposal] = useState<SequenceProposal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError(null)
    setProposal(null)
    proposeSpatialSequence(tree, measurements)
      .then(setProposal)
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false))
  }, [open, tree, measurements])

  const orderedIds = (proposal?.order || []).map((site) => site.element_id)
  // A proposal that matches the model already is a useful answer — the spatial
  // order and the one the analyst chose agree — and offering to apply it would
  // suggest otherwise.
  const wouldChange = proposal ? proposalChangesAnything(tree, orderedIds) : false
  // How many of the dates actually carry a coordinate. Counted here, on the
  // dates themselves, so that a refusal for too few SITES can be told apart
  // from too few COORDINATES: the server's message names both, but this
  // dialog can say which one applies to this model.
  const placedDates = measurements.filter(
    (m) => m.latitude != null && m.longitude != null,
  ).length

  // lg rather than md: the map is the dialog, and at md it was a 900px strip on
  // a desktop screen with room for twice that.
  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>Propose an order from the map</DialogTitle>
      <DialogContent dividers>
        {loading && <CircularProgress size={24} />}
        {error && <Alert severity="warning">{error}</Alert>}

        {proposal?.refused && (
          <Alert severity="info">
            {proposal.refused}
            {proposal.n_sites < 2 && placedDates > 0 && (
              <Typography variant="body2" sx={{ mt: 1 }}>
                <strong>
                  {placedDates} of {measurements.length} date{measurements.length === 1 ? '' : 's'}{' '}
                  {placedDates === 1 ? 'carries' : 'carry'} coordinates
                </strong>
                , so this is the model's structure rather than the data: the dates sit in a
                phase no Boundary of its own opens. Close this and use{' '}
                <em>Build from data › Group dates into sites</em>, which builds one bracketed Sequence per
                location, context or position.
              </Typography>
            )}
          </Alert>
        )}

        {proposal && !proposal.refused && (
          <>
            {/* Said before the order, not after it. */}
            <Alert severity="warning" sx={{ mb: 2 }}>
              <strong>A spanning tree is spatial adjacency, not chronology.</strong>{' '}
              Applying this asserts that everything at one site predates everything at
              the next — which the dates may refute. That is the point: accepting it
              makes the claim testable, and after a run the agreement indices will name
              the sites that do not fit.
            </Alert>

            <SiteMap
              points={proposal.order.map((site) => ({
                id: site.element_id,
                name: site.name,
                latitude: site.latitude,
                longitude: site.longitude,
              }))}
              // The spanning TREE, not the walk. A walk visits every site
              // once but jumps between branches to do it, so drawing the visit
              // order as a route would put a line between two sites nothing
              // joined -- the figure would claim an adjacency the tree never
              // asserted. The order itself is the numbered list below.
              links={proposal.edges.map((edge) => ({
                from: edge.from_point,
                to: edge.to_point,
              }))}
              height={300}
              exportName="proposed-spatial-order"
            />

            <Stack direction="row" spacing={2} sx={{ mt: 2, mb: 1 }}>
              <Typography variant="body2" color="text.secondary">
                {proposal.n_sites} sites
              </Typography>
              {proposal.total_km !== null && (
                <Typography variant="body2" color="text.secondary">
                  {Math.round(proposal.total_km).toLocaleString()} km of links
                </Typography>
              )}
            </Stack>

            <List dense>
              {proposal.order.map((site, index) => (
                <ListItem key={site.element_id} disableGutters>
                  <ListItemText
                    primary={`${index + 1}. ${site.name}`}
                    secondary={`${site.latitude.toFixed(3)}, ${site.longitude.toFixed(3)}`}
                  />
                </ListItem>
              ))}
            </List>

            {!wouldChange && (
              <Alert severity="success">
                The model is already in this order. Nothing to apply — the spatial
                order and the one you chose agree.
              </Alert>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Reject</Button>
        <Button
          variant="contained"
          disabled={!proposal || !!proposal.refused || !wouldChange}
          onClick={() => {
            onApply(orderedIds)
            onClose()
          }}
        >
          Apply this order
        </Button>
      </DialogActions>
    </Dialog>
  )
}
