/**
 * Cliente da API BotCashGain.
 *
 * VITE_API_URL pode ser definido no .env do frontend; por padrão aponta
 * direto para o backend (o backend já libera CORS).
 */
const API_URL = import.meta.env?.VITE_API_URL || 'http://127.0.0.1:8000'

class ApiError extends Error {
  constructor(status, payload) {
    const detail = payload?.detail
    const msg =
      (typeof detail === 'object' && detail?.message) ||
      (typeof detail === 'string' && detail) ||
      payload?.error ||
      `Erro HTTP ${status}`
    super(msg)
    this.status = status
    this.payload = payload
    this.code = (typeof detail === 'object' && detail?.error) || null
    this.upgradeRequired =
      (typeof detail === 'object' && detail?.upgrade_required) || false
  }
}

async function request(path, { method = 'GET', body } = {}) {
  let res
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, { message: 'Backend offline. Verifique se o servidor está rodando.' })
  }

  let payload = null
  const text = await res.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = { raw: text }
    }
  }

  if (!res.ok) throw new ApiError(res.status, payload)
  return payload
}

export const api = {
  url: API_URL,

  // Monitor de saúde
  health: () => request('/health'),
  status: () => request('/status'),
  infra: () => request('/admin/infra'),

  // Onboarding — Degraus de Confiança
  onboardingOptions: () => request('/onboarding/options'),
  sharedExtract: (data) => request('/onboarding/shared/extract', { method: 'POST', body: data }),

  // Auth OTP (Opção B — conta própria)
  authStart: (data) => request('/auth/start', { method: 'POST', body: data }),
  authComplete: (data) => request('/auth/complete', { method: 'POST', body: data }),

  // Contas
  listAccounts: () => request('/admin/accounts'),
  createSession: (data) => request('/admin/sessions', { method: 'POST', body: data }),
  updateAccountStatus: (id, status) =>
    request(`/admin/accounts/${id}`, { method: 'PATCH', body: { status } }),

  // Extração
  testExtraction: (data) => request('/test_extraction', { method: 'POST', body: data }),
  clientExtraction: (data) => request('/client/extractions', { method: 'POST', body: data }),
  getJob: (id) => request(`/admin/jobs/${id}`),
  listJobs: (limit = 20) => request(`/admin/jobs?limit=${limit}`),

  // Workers — pipeline Scraper / Audit / Warmup / Conversion
  startHarvester: (data) => request('/admin/workers/harvester', { method: 'POST', body: data }),
  startAudit: (data) => request('/admin/workers/audit', { method: 'POST', body: data }),
  startWarmup: (data) => request('/admin/workers/warmup', { method: 'POST', body: data }),
  startAdder: (data) => request('/admin/workers/adder', { method: 'POST', body: data }),
  startSender: (data) => request('/admin/workers/sender', { method: 'POST', body: data }),
  // Scraper de estrutura (groups / topics / members)
  startScraper: (data) => request('/admin/workers/scraper', { method: 'POST', body: data }),

  // Isca / Disparo
  getBait: () => request('/client/bait'),
  setBait: (message) => request('/client/bait', { method: 'PUT', body: { message } }),
  dispatch: (data) => request('/client/dispatch', { method: 'POST', body: data }),

  // Progresso / leads
  progress: () => request('/client/progress'),
  leads: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString()
    return request(`/client/leads${qs ? `?${qs}` : ''}`)
  },

  // Estrutura sincronizada (groups / topics / members)
  groups: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString()
    return request(`/admin/groups${qs ? `?${qs}` : ''}`)
  },
  groupDetail: (id) => request(`/admin/groups/${id}`),
  groupMembers: (id, params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString()
    return request(`/admin/groups/${id}/members${qs ? `?${qs}` : ''}`)
  },
}

export default api
