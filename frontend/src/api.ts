export type Cell = string | number | null
export type Dataset = {
  id: string
  synthetic: boolean
  row_count: number
  columns: {name: string; type: string; missing_count: number}[]
  preview: Record<string, Cell>[]
}
export type Example = {id: string; label: string; synthetic: boolean}
export type PlanInput = {
  dataset_id: string
  claim: string
  outcome_column: string
  group_column: string
  stratum_column: string
  positive_value: string | number
  groups: [string, string]
  target_weights: Record<string, number>
}
export type Plan = PlanInput & {id: string; confirmed: true}
export type Estimate = {
  id: string
  estimand: 'observed' | 'stratum' | 'standardized'
  group: string
  stratum: string | null
  numerator: number | null
  denominator: number | null
  value: number
  unit: 'proportion'
  formula: string
}
export type ApiError = {code: string; message: string; details: Record<string, unknown>}
export type Run = {
  id: string
  plan_id: string
  status: 'running' | 'completed' | 'failed'
  dataset_hash: string
  estimates: Estimate[]
  warnings: string[]
  explanation_status: 'not_requested' | 'ready' | 'failed'
  error: ApiError | null
}
export type Explanation = {
  run_id: string
  summary: string
  statements: {text: string; estimate_ids: string[]}[]
  limitations: string[]
}

const base = '/api/v1'
const segment = encodeURIComponent

async function request(path: string, options: RequestInit = {}, timeout = 25_000) {
  let response: Response
  try {
    response = await fetch(base + path, {
      ...options, credentials: 'include', signal: AbortSignal.timeout(timeout),
    })
  } catch (error) {
    throw new Error(error instanceof DOMException && error.name === 'TimeoutError'
      ? 'The server took too long. Try again.' : 'Could not reach the server. Try again.')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const error: ApiError | undefined = body?.error
    const detail = error?.details
    const groupDetail = detail?.group != null && detail?.stratum != null
      ? ` Group ${String(detail.group)}, stratum ${String(detail.stratum)}.` : ''
    throw new Error(error?.message ? error.message + groupDetail : `Request failed (${response.status}).`)
  }
  return response
}

async function json<T>(path: string, body?: unknown, key?: string, timeout?: number): Promise<T> {
  const response = await request(path, body === undefined ? {} : {
    method: 'POST',
    headers: {
      ...(body instanceof FormData ? {} : {'Content-Type': 'application/json'}),
      ...(key ? {'Idempotency-Key': key} : {}),
    },
    body: body instanceof FormData ? body : JSON.stringify(body),
  }, timeout)
  return response.json()
}

export const api = {
  examples: () => json<Example[]>('/examples'),
  example: (id: string) => json<Dataset>('/datasets', {example_id: id}),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return json<Dataset>('/datasets', body)
  },
  dataset: (id: string) => json<Dataset>('/datasets/' + segment(id)),
  savePlan: (plan: PlanInput) => json<Plan>('/plans', plan),
  plan: (id: string) => json<Plan>('/plans/' + segment(id)),
  startRun: (id: string, key: string) => json<Run>('/runs', {plan_id: id}, key),
  run: (id: string) => json<Run>('/runs/' + segment(id)),
  explain: (id: string) => json<Explanation>('/runs/' + segment(id) + '/explanation', {}, undefined, 50_000),
  download: async (id: string, includeData: boolean) => {
    const response = await request('/runs/' + segment(id) + '/export?include_data=' + includeData)
    if (response.headers.get('Content-Type')?.split(';')[0] !== 'application/zip') {
      throw new Error('The server did not return a ZIP archive.')
    }
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `researchlens-${id}.zip`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
  },
}
