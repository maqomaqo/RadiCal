import axios from 'axios'

// Create axios instance with default base URL
const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
})

export interface CalibrateRequest {
  age_bp: number
  error_1sigma: number
  curve_id?: string
}

export interface HPDRange {
  min: number
  max: number
  probability_percent: number
}

export interface CalibrateResponse {
  success: boolean
  data: {
    id: string
    calibrated_age_bp: number
    hpd_68: { min: number; max: number }
    hpd_95: { min: number; max: number }
    // Full HPD breakdown: one or more disjoint calendar-age ranges (curves
    // are non-monotonic, so the posterior can be multimodal), each with
    // its own probability_percent share of the total distribution.
    hpd_68_ranges?: HPDRange[]
    hpd_95_ranges?: HPDRange[]
    probability_distribution: Array<[number, number]>
  }
}

export interface BayesianRequest {
  name: string
  measurements: Array<{
    id: string
    sample_name: string
    age_bp: number
    error_1sigma: number
    material_type: string
    sample_type: 'terrestrial' | 'marine'
    curve_id: string
  }>
  relationships?: Array<{
    from_sample_id: string
    to_sample_id: string
    relationship: string
  }>
  bayesian_config?: {
    solver: 'auto' | 'exact' | 'gibbs'
    mcmc_samples: number
    mcmc_tune: number
    mcmc_chains: number
    uniform_span_prior: boolean
  }
}

export const apiService = {
  // Calibration endpoints
  calibrateSingle: (request: CalibrateRequest) =>
    apiClient.post<CalibrateResponse>('/api/analysis/calibrate', request),

  bayesianAnalysis: (request: BayesianRequest) =>
    apiClient.post('/api/analysis/bayesian', request),

  getAnalysis: (id: string) => apiClient.get(`/api/analysis/${id}`),

  listAnalyses: (page = 1, perPage = 10) =>
    apiClient.get('/api/analysis', { params: { page, per_page: perPage } }),

  // Curve endpoints
  getCurves: () => apiClient.get('/api/curves'),

  getCurve: (id: string) => apiClient.get(`/api/curves/${id}`),

  getCurvesByType: (type: string) => apiClient.get(`/api/curves/type/${type}`),

  // Results endpoints
  exportCSV: (analysisId: string) =>
    apiClient.get(`/api/results/${analysisId}/export/csv`),

  exportJSON: (analysisId: string) =>
    apiClient.get(`/api/results/${analysisId}/export/json`),

  exportPDF: (analysisId: string) =>
    apiClient.get(`/api/results/${analysisId}/pdf`),

  // Harris Matrix endpoints
  validateMatrix: (data: { nodes: Array<any>; edges: Array<any> }) =>
    apiClient.post('/api/harris-matrix/validate', data),

  generateRelationships: (data: { nodes: Array<any>; edges: Array<any> }) =>
    apiClient.post('/api/harris-matrix/relationships', data),
}

// Alias for backwards compatibility
export const calibrationCurvesAPI = {
  list: () => apiService.getCurves(),
}
