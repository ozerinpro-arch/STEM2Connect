import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { api } from './api'
import type { Dataset, Estimate, Example, Explanation, Plan, PlanInput, Run } from './api'
import './style.css'

const formatNumber = new Intl.NumberFormat('en')
const percent = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 2 })
const weightsFormat = new Intl.NumberFormat('en', {maximumFractionDigits: 8})
const examplePlan = {
  outcome_column: 'success', group_column: 'system', stratum_column: 'difficulty',
  positive_value: 1, groups: ['A', 'B'] as [string, string], target_weights: {easy: 0.5, hard: 0.5},
}
const message = (error: unknown) => error instanceof Error ? error.message : 'An unexpected error occurred.'
const titleOf = (item: Estimate) => item.estimand === 'observed'
  ? 'Observed overall' : item.estimand === 'standardized' ? 'Standardized' : String(item.stratum)
const themeKey = 'researchlens.theme'
const historyKey = 'researchlens.runs'

function storage(key: string, value?: string) {
  try {
    if (value === undefined) return localStorage.getItem(key)
    localStorage.setItem(key, value)
  } catch {
    return null
  }
}

function runHistory(): string[] {
  try {
    const ids: unknown = JSON.parse(storage(historyKey) || '[]')
    return Array.isArray(ids) ? ids.filter(id => typeof id === 'string' && /^run_[a-f0-9]+$/.test(id)).slice(0, 8) : []
  } catch {
    return []
  }
}

function updateRunUrl(id?: string) {
  const url = new URL(location.href)
  if (id) url.searchParams.set('run', id)
  else url.searchParams.delete('run')
  history.replaceState(null, '', url)
}

function Preview({ dataset }: {dataset: Dataset}) {
  return <details className="preview">
    <summary>Preview CSV <span>{dataset.preview.length} of {formatNumber.format(dataset.row_count)} rows</span></summary>
    <div className="scroll" tabIndex={0} role="region" aria-label="CSV preview">
      <table>
        <thead><tr>{dataset.columns.map(column => <th scope="col" key={column.name}>{column.name}
          <small>{column.type}{column.missing_count ? ` · ${column.missing_count} missing` : ''}</small></th>)}</tr></thead>
        <tbody>{dataset.preview.map((row, index) => <tr key={index}>{dataset.columns.map(column =>
          <td key={column.name}>{row[column.name] == null ? <span className="muted">Missing</span> : String(row[column.name])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </details>
}

function valuesFor(dataset: Dataset, column: string): string[] {
  return [...new Set(dataset.preview.flatMap(row => row[column] == null ? [] : [String(row[column])]))]
}

function equalWeights(names: string[]) {
  return names.slice(0, 100).map(name => ({name, weight: String(1 / names.length)}))
}

function PlanEditor({dataset, initial, missingStrata, busy, onSubmit}: {
  dataset: Dataset; initial?: Plan; missingStrata: string[]; busy: boolean; onSubmit: (plan: PlanInput) => void
}) {
  const names = dataset.columns.map(column => column.name)
  const [outcome, setOutcome] = useState(initial?.outcome_column ?? (names.includes('success') ? 'success' : ''))
  const [group, setGroup] = useState(initial?.group_column ?? (names.includes('system') ? 'system' : ''))
  const [stratum, setStratum] = useState(initial?.stratum_column ?? (names.includes('difficulty') ? 'difficulty' : ''))
  const [positive, setPositive] = useState(String(initial?.positive_value ?? (outcome === 'success' ? '1' : '')))
  const [groups, setGroups] = useState(initial?.groups ?? [valuesFor(dataset, group)[0] ?? '', valuesFor(dataset, group)[1] ?? ''])
  const [weights, setWeights] = useState(initial
    ? [
      ...Object.entries(initial.target_weights).map(([name, weight]) => ({name, weight: String(weight)})),
      ...missingStrata.filter(name => !Object.hasOwn(initial.target_weights, name))
        .slice(0, 100 - Object.keys(initial.target_weights).length).map(name => ({name, weight: ''})),
    ]
    : equalWeights(valuesFor(dataset, stratum)))
  const [claim, setClaim] = useState(initial?.claim ?? '')
  const [error, setError] = useState('')
  const sum = weights.reduce((total, item) => total + Number(item.weight), 0)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (new Set([outcome, group, stratum]).size !== 3) {
      setError('Choose three different columns.')
      return
    }
    if (!groups.every(value => value.trim()) || groups[0] === groups[1] || !positive.trim()) {
      setError('Enter two distinct group values and a positive outcome.')
      return
    }
    if (!weights.length || weights.some(item => !item.name.trim() || !item.weight.trim()) ||
        new Set(weights.map(item => item.name)).size !== weights.length || Math.abs(sum - 1) > 1e-12) {
      setError('Enter each stratum once and assign weights that add up to 1.')
      return
    }
    setError('')
    onSubmit({
      dataset_id: dataset.id, claim: claim.trim(), outcome_column: outcome, group_column: group,
      stratum_column: stratum, positive_value: positive, groups: [groups[0], groups[1]],
      target_weights: Object.fromEntries(weights.map(item => [item.name, Number(item.weight)])),
    })
  }

  return <form className="editor" onSubmit={submit}>
    <fieldset disabled={busy}>
      <label>Research claim<textarea rows={2} required maxLength={4000} value={claim}
        onChange={event => setClaim(event.target.value)} placeholder="Which group has the higher success rate?" /></label>
      <div className="fields">
        <label>Outcome column<select required value={outcome} onChange={event => { setOutcome(event.target.value); setPositive('') }}>
          <option value="">Choose a column</option>{names.map(name => <option key={name}>{name}</option>)}
        </select></label>
        <label>Positive outcome<input required maxLength={4096} list="outcomes" value={positive}
          onChange={event => setPositive(event.target.value)} /></label>
        <datalist id="outcomes">{valuesFor(dataset, outcome).map(value => <option key={value} value={value} />)}</datalist>
        <label>Group column<select required value={group} onChange={event => {
          setGroup(event.target.value)
          const values = valuesFor(dataset, event.target.value)
          setGroups([values[0] ?? '', values[1] ?? ''])
        }}><option value="">Choose a column</option>{names.map(name => <option key={name}>{name}</option>)}</select></label>
        <label>Stratify by<select required value={stratum} onChange={event => {
          setStratum(event.target.value)
          setWeights(equalWeights(valuesFor(dataset, event.target.value)))
        }}><option value="">Choose a column</option>{names.map(name => <option key={name}>{name}</option>)}</select></label>
        {groups.map((value, i) => <label key={i}>Group {i + 1}<input required maxLength={4096} list="groups"
          value={value} onChange={event => setGroups(groups.map((entry, index) => i === index ? event.target.value : entry))} /></label>)}
        <datalist id="groups">{valuesFor(dataset, group).map(value => <option key={value} value={value} />)}</datalist>
      </div>
      <div className="section-head"><h3>Target population weights</h3><span className="muted">Total {weightsFormat.format(sum)}</span></div>
      <p className="muted">Values come from the preview. Add any other strata present in the selected groups.</p>
      <div className="weight-list">
        {weights.map((item, i) => <div className="weight-row" key={i}>
          <input aria-label={`Stratum ${i + 1}`} placeholder="Stratum value" required maxLength={4096}
            value={item.name} onChange={event => setWeights(weights.map((entry, index) => i === index ? {...entry, name: event.target.value} : entry))} />
          <input aria-label={`Weight for stratum ${i + 1}`} type="number" min="0" max="1" step="any" required
            value={item.weight} onChange={event => setWeights(weights.map((entry, index) => i === index ? {...entry, weight: event.target.value} : entry))} />
          <button type="button" className="square" aria-label={`Remove stratum ${i + 1}`}
            onClick={() => setWeights(weights.filter((_, index) => i !== index))}>×</button>
        </div>)}
      </div>
      <div className="actions">
        <button type="button" disabled={weights.length >= 100}
          onClick={() => setWeights([...weights, {name: '', weight: ''}])}>Add stratum</button>
        <button type="button" disabled={!weights.length}
          onClick={() => setWeights(equalWeights(weights.map(item => item.name)))}>Equal weights</button>
      </div>
      {error && <p className="alert" role="alert">{error}</p>}
      <button className="primary" type="submit">Confirm plan &amp; run <span aria-hidden="true">→</span></button>
    </fieldset>
  </form>
}

function EstimateChart({title, description, estimates, order, active, onSelect}: {
  title: string; description: string; estimates: Estimate[]; order: readonly string[];
  active?: string; onSelect: (id: string) => void
}) {
  return <section className="chart">
    <h3>{title}</h3><p>{description}</p>
    <div className="axis" aria-hidden="true"><span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span></div>
    {estimates.map(item => <button key={item.id} type="button" className="bar-row" aria-pressed={active === item.id}
      aria-label={`${title}, group ${item.group}, ${percent.format(item.value)}. Inspect estimate.`}
      onClick={() => onSelect(item.id)}>
      <span className="bar-name" title={item.group}>{item.group}</span>
      <span className="track" aria-hidden="true"><span className={order.indexOf(item.group) === 0 ? 'bar' : 'bar alternate'}
        style={{width: String(item.value * 100) + "%"}} /></span>
      <strong>{percent.format(item.value)}</strong>
    </button>)}
  </section>
}

function ranking(estimates: Estimate[]) {
  if (estimates.length !== 2 || estimates[0].value === estimates[1].value) return null
  return estimates[estimates[0].value > estimates[1].value ? 0 : 1].group
}

function PopulationMix({slice, observed, withinSlice, target}: {
  slice: string; observed: Estimate[]; withinSlice: Estimate[]; target: number
}) {
  const rows = observed.flatMap(item => {
    const count = withinSlice.find(estimate => estimate.group === item.group)?.denominator
    if (count == null || !item.denominator) return []
    return [{name: `Group ${item.group}`, value: count / item.denominator,
      detail: `${formatNumber.format(count)} of ${formatNumber.format(item.denominator)} observations`}]
  })
  return <section className="mix" aria-label="Sample composition">
    <div>
      <h3>Who is in the sample?</h3>
      <p>Share of observations in <strong>{slice}</strong>. Compare the collected samples with the target mix.</p>
    </div>
    <div className="mix-rows">
      {[...rows, {name: 'Target mix', value: target, detail: 'Weight applied to both groups'}].map(row =>
        <div className="mix-row" key={row.name}>
          <span title={row.name}>{row.name}</span>
          <div className="mix-track" aria-hidden="true"><span style={{width: `${row.value * 100}%`}} /></div>
          <strong>{percent.format(row.value)}</strong>
          <small>{row.detail}</small>
        </div>)}
    </div>
  </section>
}

function MixExperiment({plan, run, previous, busy, onApply}: {
  plan: Plan; run: Run; previous?: Run; busy: boolean;
  onApply: (weights: Record<string, number>) => void
}) {
  const [first, second] = Object.keys(plan.target_weights).sort()
  const initial = plan.target_weights[first]
  const [draft, setWeight] = useState<number>()
  const weight = draft ?? initial * 100
  const firstPercent = weightsFormat.format(weight)
  const secondPercent = weightsFormat.format(100 - weight)
  const before = previous?.estimates.filter(item => item.estimand === 'standardized')
  const after = run.estimates.filter(item => item.estimand === 'standardized')
  return <section className="mix-experiment">
    <h3>Try a different target mix</h3>
    <p className="muted">Change the population weights and run a new analysis on the same observations.</p>
    <label className="mix-slider">{first} <strong>{firstPercent}%</strong>
      <input type="range" min="0" max="100" step="any" value={weight} aria-label={`Target weight for ${first}`}
        aria-valuetext={`${firstPercent}% ${first}, ${secondPercent}% ${second}`} disabled={busy}
        onChange={event => setWeight(Number(event.target.value))} />
    </label>
    <div className="section-head">
      <p className="muted">{second} {secondPercent}%</p>
      <button type="button" disabled={busy || draft === undefined || weight / 100 === initial}
        onClick={() => onApply({[first]: weight / 100, [second]: (100 - weight) / 100})}>Apply target mix</button>
    </div>
    {before?.length === 2 && <table className="mix-change">
      <caption>Standardized rates after reweighting</caption>
      <thead><tr><th scope="col">Group</th><th scope="col">Previous mix</th><th scope="col">Current mix</th></tr></thead>
      <tbody>{after.map(item => <tr key={item.id}><th scope="row">{item.group}</th>
        <td>{percent.format(before.find(estimate => estimate.group === item.group)!.value)}</td>
        <td>{percent.format(item.value)}</td></tr>)}</tbody>
    </table>}
  </section>
}

function Results({run, plan, selected, previous, busy, onSelect, onApply}: {
  run: Run; plan: Plan; selected?: string; previous?: Run; busy: boolean;
  onSelect: (id: string) => void; onApply: (weights: Record<string, number>) => void
}) {
  const observed = run.estimates.filter(item => item.estimand === 'observed')
  const adjusted = run.estimates.filter(item => item.estimand === 'standardized')
  const strata = [...new Set(run.estimates.flatMap(item => item.stratum == null ? [] : [item.stratum]))]
  const [slice, setSlice] = useState(strata[0] ?? '')
  const withinSlice = run.estimates.filter(item => item.stratum === slice)
  const rawLeader = ranking(observed)
  const adjustedLeader = ranking(adjusted)
  const reversed = rawLeader !== null && adjustedLeader !== null && rawLeader !== adjustedLeader

  return <>
    <div className="results-intro">
      <div className="eyebrow">Evidence</div>
      <h2>{reversed ? 'The ranking reverses' : rawLeader !== null && rawLeader === adjustedLeader ? 'The ranking holds' : 'Compare the population estimates'}</h2>
      <p>{reversed ? `Group ${rawLeader} leads in the observed sample. Group ${adjustedLeader} leads under the specified population mix.`
        : rawLeader !== null && rawLeader === adjustedLeader ? `Group ${rawLeader} has the higher rate in both population estimates.`
        : 'The observed sample and the specified population mix answer different questions.'}</p>
    </div>
    <div className="chart-grid">
      <EstimateChart title="Observed overall" description="Each group keeps its own sample mix."
        estimates={observed} order={plan.groups} active={selected} onSelect={onSelect} />
      <EstimateChart title="Standardized" description="Both groups use the same target mix."
        estimates={adjusted} order={plan.groups} active={selected} onSelect={onSelect} />
    </div>
    {strata.length > 0 && <section className="strata">
      <div className="section-head"><h3>Within each stratum</h3><label className="inline-label">Stratum<select aria-label="Stratum" value={slice}
        onChange={event => setSlice(event.target.value)}>{strata.map(name => <option key={name}>{name}</option>)}</select></label></div>
      <EstimateChart title={slice} description={`Target weight ${percent.format(plan.target_weights[slice])}`}
        estimates={withinSlice} order={plan.groups} active={selected} onSelect={onSelect} />
      <PopulationMix slice={slice} observed={observed} withinSlice={withinSlice} target={plan.target_weights[slice]} />
    </section>}
    {strata.length === 2 && <MixExperiment plan={plan} run={run} previous={previous} busy={busy} onApply={onApply} />}
    <details className="preview"><summary>View all estimates as a table</summary>
      <div className="scroll" tabIndex={0} role="region" aria-label="All estimates">
        <table><thead><tr><th scope="col">Estimate</th><th scope="col">Group</th><th scope="col">Rate</th><th scope="col">Count</th></tr></thead>
          <tbody>{run.estimates.map(item => <tr key={item.id}><td><button className="text-button"
            onClick={() => onSelect(item.id)}>{titleOf(item)}</button></td><td>{item.group}</td>
            <td>{percent.format(item.value)}</td><td>{item.denominator === null ? 'Weighted' : `${item.numerator} / ${item.denominator}`}</td></tr>)}</tbody></table>
      </div>
    </details>
  </>
}

function EstimateDetails({item, plan}: {item: Estimate; plan: Plan}) {
  return <>
    <div className="eyebrow">Estimate details</div>
    <h2>{titleOf(item)}</h2>
    <span className="muted">Group {item.group}</span>
    <div className="metric">{percent.format(item.value)}</div>
    {item.denominator !== null
      ? <p>{formatNumber.format(item.numerator ?? 0)} positive outcomes out of {formatNumber.format(item.denominator)} observations.</p>
      : <p>Weighted average of the group’s stratum rates. Weights define the target population.</p>}
    <h3>Formula</h3><code className="formula">{item.formula}</code>
    {item.estimand === 'standardized' && <dl className="weights">
      {Object.entries(plan.target_weights).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{percent.format(value)}</dd></div>)}
    </dl>}
  </>
}

function App() {
  const [examples, setExamples] = useState<Example[]>([])
  const [dataset, setDataset] = useState<Dataset>()
  const [plan, setPlan] = useState<Plan>()
  const [run, setRun] = useState<Run>()
  const [previous, setPrevious] = useState<Run>()
  const [selected, setSelected] = useState<string>()
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [explanation, setExplanation] = useState<Explanation>()
  const [includeCSV, setIncludeCSV] = useState(false)
  const [consent, setConsent] = useState(false)
  const [saved, setSaved] = useState(runHistory)
  const [theme, setTheme] = useState(() => storage(themeKey) ?? 'system')
  const fileInput = useRef<HTMLInputElement>(null)
  const destination = useRef<HTMLElement>(null)
  const estimatePanel = useRef<HTMLElement>(null)
  const retryKey = useRef<{plan: string; key: string} | undefined>(undefined)
  const active = run?.estimates.find(item => item.id === selected) ?? run?.estimates[0]
  const missing = run?.error?.details.strata
  const missingStrata: string[] = Array.isArray(missing)
    ? missing.filter((name): name is string => typeof name === 'string') : []

  function setExperiment(nextDataset: Dataset, nextPlan: Plan, nextRun: Run, prior?: Run) {
    setPrevious(prior)
    setDataset(nextDataset)
    setPlan(nextPlan)
    setRun(nextRun)
    setEditing(false)
    setSelected(nextRun.estimates[0]?.id)
    setExplanation(undefined)
    setIncludeCSV(false)
    setConsent(false)
    updateRunUrl(nextRun.id)
    const nextSaved = [nextRun.id, ...runHistory().filter(id => id !== nextRun.id)].slice(0, 8)
    storage(historyKey, JSON.stringify(nextSaved))
    setSaved(nextSaved)
  }

  async function restore(id: string) {
    const previous = await api.run(id)
    const confirmed = await api.plan(previous.plan_id)
    const source = await api.dataset(confirmed.dataset_id)
    setExperiment(source, confirmed, previous)
    if (previous.explanation_status === 'ready') {
      try { setExplanation(await api.explain(previous.id)) } catch { /* The result remains available. */ }
    }
  }

  useEffect(() => {
    api.examples().then(setExamples).catch(() => setNotice('Examples are unavailable. You can still try uploading a CSV.'))
    const id = new URL(location.href).searchParams.get('run')
    if (id) {
      setBusy('Opening experiment')
      restore(id).catch(() => {
        updateRunUrl()
        setError('This experiment is unavailable. Start a new run in this browser.')
      }).finally(() => setBusy(''))
    }
  }, [])

  useEffect(() => {
    if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme
    else delete document.documentElement.dataset.theme
  }, [theme])

  function focusWorkspace() {
    requestAnimationFrame(() => destination.current?.focus())
  }

  async function action(label: string, perform: () => Promise<void>) {
    setBusy(label)
    setError('')
    setNotice('')
    try { await perform() } catch (caught) { setError(message(caught)) }
    finally { setBusy('') }
  }

  async function calculate(nextPlan: Plan) {
    if (retryKey.current?.plan !== nextPlan.id) {
      retryKey.current = {plan: nextPlan.id, key: crypto.randomUUID()}
    }
    const current = await api.startRun(nextPlan.id, retryKey.current.key)
    let result = current
    const deadline = Date.now() + 60_000
    while (result.status === 'running' && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 2000))
      result = await api.run(current.id)
    }
    retryKey.current = undefined
    return result
  }

  function loadExample(id: string) {
    void action('Running synthetic example', async () => {
      const source = await api.example(id)
      const nextPlan = await api.savePlan({
        ...examplePlan, dataset_id: source.id, claim: id === 'stable-search'
          ? 'System A has a higher success rate than B' : 'System B has a higher success rate than A',
      })
      setExperiment(source, nextPlan, await calculate(nextPlan))
      focusWorkspace()
    })
  }

  function openUpload(file: File | undefined) {
    if (!file) return
    void action('Reading CSV', async () => {
      if (file.size > 10_000_000) throw new Error('Choose a CSV file of 10 MB or less.')
      const source = await api.upload(file)
      setDataset(source)
      setPlan(undefined)
      setRun(undefined)
      setExplanation(undefined)
      setEditing(true)
      updateRunUrl()
      focusWorkspace()
    })
  }

  function confirm(draft: PlanInput) {
    if (!dataset) return
    void action('Calculating estimates', async () => {
      const confirmed = await api.savePlan(draft)
      try {
        setExperiment(dataset, confirmed, await calculate(confirmed))
      } catch (caught) {
        setPlan(confirmed)
        setRun(undefined)
        setEditing(false)
        updateRunUrl()
        throw caught
      }
      focusWorkspace()
    })
  }

  function recalculate() {
    if (!plan || !dataset) return
    void action('Calculating estimates', async () => {
      setExperiment(dataset, plan, await calculate(plan))
      focusWorkspace()
    })
  }

  function applyMix(target_weights: Record<string, number>) {
    if (!dataset || !plan || run?.status !== 'completed') return
    void action('Recalculating for the new target mix', async () => {
      const {id: _, confirmed: __, ...input} = plan
      const updated = await api.savePlan({...input, target_weights})
      setExperiment(dataset, updated, await calculate(updated), run)
      setNotice('Target mix changed. The previous and current rates are shown below.')
      focusWorkspace()
    })
  }

  function chooseEstimate(id: string) {
    setSelected(id)
    if (window.matchMedia('(max-width: 1040px)').matches) {
      requestAnimationFrame(() => estimatePanel.current?.focus())
    }
  }

  return <div className="shell" aria-busy={Boolean(busy)}>
    <a className="skip" href="#workspace">Skip to workspace</a>
    <header className="top glass">
      <a className="brand" href="/" aria-label="ResearchLens home">
        <span className="mark" aria-hidden="true"><span /><span /><span /></span>
        <span>Research<span className="brand-accent">Lens</span><small>Research workspace</small></span>
      </a>
      <div className="top-actions">
        {saved.length > 0 && <label className="inline-label history-label"><span>Runs</span><select
          aria-label="Recent experiments" value={run?.id ?? ''} disabled={Boolean(busy)}
          onChange={event => {
            const id = event.target.value
            if (id) void action('Opening experiment', async () => { await restore(id); focusWorkspace() })
          }}>
          <option value="">Recent experiments</option>
          {saved.map((id, index) => <option value={id} key={id}>Experiment {saved.length - index} · {id.slice(-6)}</option>)}
        </select></label>}
        <label className="inline-label"><span className="theme-label">Theme</span><select aria-label="Theme" value={theme}
          onChange={event => { setTheme(event.target.value); storage(themeKey, event.target.value) }}>
          <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
        </select></label>
      </div>
    </header>

    <main id="workspace">
      <section className={dataset ? 'hero compact glass' : 'hero glass'}>
        <div>
          <div className="eyebrow">ResearchLens Lab</div>
          <h1>How much does the sample shape the result?</h1>
          <p>Compare observed rates, open the evidence for each group, and rerun the experiment under a shared population mix.</p>
          <div className="actions">
            <button className="primary" disabled={Boolean(busy)} onClick={() => fileInput.current?.click()}>
              Import CSV <span aria-hidden="true">↑</span>
            </button>
            <input hidden ref={fileInput} type="file" accept=".csv,text/csv" onChange={event => {
              openUpload(event.currentTarget.files?.[0]); event.currentTarget.value = ''
            }} />
          </div>
        </div>
        {!dataset && <div className="examples">
          <h2>Try a synthetic example</h2>
          {(examples.length ? examples : [
            {id: 'simpson-search', label: 'Search benchmark', synthetic: true},
            {id: 'stable-search', label: 'Stable comparison', synthetic: true},
          ]).map(example =>
            <button className="example" key={example.id} disabled={Boolean(busy)} onClick={() => loadExample(example.id)}>
              <span><strong>{example.label}</strong><small>{example.id === 'simpson-search'
                ? 'A surprising change in ranking' : 'A ranking that holds across groups'}</small></span>
              <span className="arrow" aria-hidden="true">↗</span>
            </button>)}
          <p className="muted">CSV uploads accept up to 10 MB and 50,000 observations.</p>
        </div>}
      </section>

      <div className="status" role="status">{busy ? <><span className="spinner" aria-hidden="true" />{busy}…</> : notice}</div>
      {error && <div className="alert" role="alert">{error}</div>}

      {dataset && <div className="workspace" data-has-details={Boolean(run?.estimates.length)}>
        <aside className="glass dataset">
          <div className="eyebrow">Data and plan</div>
          <h2>{dataset.synthetic ? 'Synthetic example' : 'Imported CSV'}</h2>
          <div className="dataset-size">{formatNumber.format(dataset.row_count)} <span>observations</span></div>
          <p className="muted">{dataset.columns.length} columns</p>
          <Preview dataset={dataset} />
          {plan && !editing && <div className="plan-summary">
            <h3>Confirmed plan</h3>
            <blockquote>{plan.claim}</blockquote>
            <dl className="weights">
              <div><dt>Outcome</dt><dd>{plan.outcome_column} = {plan.positive_value}</dd></div>
              <div><dt>Groups</dt><dd>{plan.groups.join(' / ')}</dd></div>
              <div><dt>Stratify by</dt><dd>{plan.stratum_column}</dd></div>
            </dl>
            <details className="preview"><summary>Target weights</summary><dl className="weights">
              {Object.entries(plan.target_weights).map(([name, value]) =>
                <div key={name}><dt>{name}</dt><dd>{percent.format(value)}</dd></div>)}
            </dl></details>
            <div className="actions">
              <button disabled={Boolean(busy)} onClick={() => {setEditing(true); focusWorkspace()}}>Edit plan</button>
              {run?.status !== 'running' && <button disabled={Boolean(busy)} onClick={recalculate}>{run ? 'Rerun' : 'Retry calculation'}</button>}
            </div>
          </div>}
        </aside>
        <section className="glass evidence" tabIndex={-1} ref={destination} aria-label="Analysis workspace">
          {editing || !plan ? <>
            <div className="eyebrow">Analysis plan</div><h2>Define the comparison</h2>
            <PlanEditor key={plan?.id ?? dataset.id} dataset={dataset} initial={plan} missingStrata={missingStrata}
              busy={Boolean(busy)} onSubmit={confirm} />
            {plan && <button className="text-button" disabled={Boolean(busy)} onClick={() => setEditing(false)}>Cancel editing</button>}
          </> : run?.status === 'completed' ? <Results key={run.id} run={run} plan={plan} selected={active?.id}
            previous={previous} busy={Boolean(busy)} onSelect={chooseEstimate} onApply={applyMix} />
            : run?.status === 'failed' ? <div className="alert" role="alert"><h2>Calculation needs a change</h2>
              <p>{run.error?.message}</p>
              {missingStrata.length > 0 && <p>Assign weights to these additional strata: {missingStrata.join(', ')}.</p>}
              {typeof run.error?.details.group === 'string' && <p>Group {run.error.details.group}, stratum {String(run.error.details.stratum)}.</p>}
              <button onClick={() => setEditing(true)}>Edit plan</button></div>
            : <><h2>{run?.status === 'running' ? 'Calculation in progress' : 'Plan is ready'}</h2>
              {run?.status === 'running' && <button onClick={() => void action('Refreshing calculation', () => restore(run.id))}>Refresh result</button>}</>}
        </section>
        {!editing && run?.status === 'completed' && active && plan &&
          <aside className="glass evidence-details" tabIndex={-1} ref={estimatePanel} aria-label="Estimate details">
            <EstimateDetails item={active} plan={plan} />
            <div className="export">
              <h3>Reproduce this experiment</h3>
              <p>Download the plan, estimates and a Python script that runs independently.</p>
              <label className="check"><input type="checkbox" checked={includeCSV}
                onChange={event => setIncludeCSV(event.target.checked)} />Include the source CSV</label>
              <button className="primary" disabled={Boolean(busy)} onClick={() => void action('Preparing ZIP', async () => {
                await api.download(run.id, includeCSV)
                setNotice('ZIP download started.')
              })}>Download ZIP <span aria-hidden="true">↓</span></button>
            </div>
          </aside>}
      </div>}

      {!editing && run?.status === 'completed' && <section className="glass interpretation">
        <div>
          <div className="eyebrow">Interpretation</div>
          <h2>What the comparison supports</h2>
          <p>The observed rates describe the samples as collected. Standardized rates use the weights in your plan. These comparisons are descriptive and do not establish causality.</p>
          {run.warnings.filter(warning => warning !== 'Synthetic demonstration data').map(warning => <p className="muted" key={warning}>{warning}.</p>)}
        </div>
        <div>
          <h3>AI explanation</h3>
          {explanation ? <>
            <p>{explanation.summary}</p>
            <p className="muted">Group aliases: G1 = {plan?.groups[0]}, G2 = {plan?.groups[1]}.
              {plan && ` Strata: ${[...new Set(run.estimates.flatMap(item => item.stratum == null ? [] : [item.stratum]))]
                .slice(0, 2).map((name, i) => `S${i + 1} = ${name}`).join(', ')}.`}</p>
            <ul>{explanation.statements.map((statement, index) => <li key={index}>{statement.text}
              <div className="actions">{statement.estimate_ids.map((id, i) => {
                const estimate = run.estimates.find(item => item.id === id)
                return estimate && <button key={i} className="text-button" onClick={() => {
                  chooseEstimate(id)
                  requestAnimationFrame(() => estimatePanel.current?.focus())
                }}>{titleOf(estimate)} · {estimate.group}</button>
              })}</div></li>)}</ul>
            {explanation.limitations.length > 0 && <ul>{explanation.limitations.map((limit, i) => <li key={i}>{limit}</li>)}</ul>}
          </> : <>
            <p>Share aggregate estimates and anonymous group labels with the configured model.</p>
            {run.explanation_status === 'failed' && <p className="muted">The last explanation request failed. Your estimates are available.</p>}
            <label className="check"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />
              Allow sharing aggregate estimates</label>
            <button disabled={!consent || Boolean(busy)} onClick={() => void action('Requesting explanation', async () => {
              try { setExplanation(await api.explain(run.id)) }
              catch (caught) {
                setRun({...run, explanation_status: 'failed'})
                throw caught
              }
            })}>Generate explanation</button>
          </>}
        </div>
      </section>}
    </main>
    <footer><span>ResearchLens</span><span>STEM2Connect · Scientific Research</span></footer>
  </div>
}

createRoot(document.getElementById('root')!).render(<App />)
