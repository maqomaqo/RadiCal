import { useEffect, useState } from 'react'
import { fetchCurves, type CurveSpec } from '@/api/curves'

interface UseCurves {
  curves: CurveSpec[]
  loading: boolean
  /** Message to show beside a curve picker when the list could not be loaded. */
  error: string | null
}

/**
 * The available calibration curves, for any component that offers a choice
 * of one.
 *
 * The underlying request is shared across components (api/curves.ts), so
 * mounting this in several forms at once costs one request. An unmount
 * mid-flight is guarded, since setting state on an unmounted component is
 * harmless but noisy.
 */
export function useCurves(): UseCurves {
  const [curves, setCurves] = useState<CurveSpec[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    fetchCurves()
      .then((list) => {
        if (!active) return
        setCurves(list)
        setError(null)
      })
      .catch(() => {
        if (!active) return
        setError('Could not load the calibration curve list.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return { curves, loading, error }
}
