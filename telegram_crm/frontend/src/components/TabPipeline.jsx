import { useCallback, useEffect, useState } from 'react'
import { useJobPoll } from '../hooks/useJobPoll'
import api from '../api'
import { Badge, Button, Card } from './ui'

const PIPELINE = [
  { key: 'scraper', label: 'Scraper', desc: 'Extrai leads do grupo', icon: '⛏' },
  { key: 'audit', label: 'Audit', desc: 'Valida e limpa a base', icon: '🛡' },
  { key: 'warmup', label: 'Warmup', desc: 'Aquece contas anti-ban', icon: '🔥' },
  { key: 'conversion', label: 'Conversion', desc: 'Dispara a isca', icon: '➤' },
]

function StepCard({ step, job, busy, onRun, children }) {
  const pct = job ? (job.status === 'done' ? 100 : (job.progress_pct ?? 0)) : 0
  const tone = !job ? 'slate' : job.status === 'done' ? 'emerald' : job.status === 'failed' ? 'rose' : job.status === 'running' ? 'sky' : 'slate'
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="text-xl">{step.icon}</span>
          <div>
            <p className="text-sm font-bold text-slate-100">{step.label}</p>
            <p className="text-xs text-slate-500">{step.desc}</p>
          </div>
        </div>
        {job && <Badge tone={tone}>{job.status} · {pct}%</Badge>}
      </div>
      {job && (
        <div className="mb-3 h-2 overflow-hidden rounded-full bg-slate-800">
          <div className={`h-full rounded-full transition-all duration-500 ${job.status === 'failed' ? 'bg-rose-500' : 'bg-gradient-to-r from-emerald-400 to-teal-400'}`} style={{ width: `${pct}%` }} />
        </div>
      )}
      {children}
      <Button onClick={onRun} loading={busy} variant={job ? 'secondary' : 'primary'} className="mt-3 w-full">
        {busy ? 'Executando...' : job ? 'Executar novamente' : `Executar ${step.label}`}
      </Button>
      {job?.error && <p className="mt-2 text-xs text-rose-400">{job.error}</p>}
    </Card>
  )
}

export default function TabPipeline({ showToast }) {
  const scraper = useJobPoll(showToast)
  const audit = useJobPoll(showToast)
  const warmup = useJobPoll(showToast)
  const sender = useJobPoll(showToast)
  const [group, setGroup] = useState('')
  const [limit, setLimit] = useState(50)

  const runAudit = useCallback(async () => {
    const res = await api.startAudit({ limit: 1000 })
    showToast(`Audit #${res.job_id} iniciado!`, 'success')
    audit.watch(res.job_id, 'Auditoria')
  }, [audit, showToast])

  const runWarmup = useCallback(async () => {
    const res = await api.startWarmup({ rounds: 2 })
    showToast(`Warmup #${res.job_id} iniciado!`, 'success')
    warmup.watch(res.job_id, 'Warmup')
  }, [warmup, showToast])

  return (
    <div className="space-y-5">
      <Card>
        <h2 className="mb-1 text-sm font-semibold text-slate-200">Fluxo de dados</h2>
        <p className="font-mono text-xs text-slate-500">Scraper → DB → Audit → DB → Conversion (Sender/Adder)</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {PIPELINE.map((s, i) => (
            <span key={s.key} className="flex items-center gap-2">
              <span className="rounded-lg bg-slate-800 px-2.5 py-1 text-xs font-semibold text-slate-300">{s.icon} {s.label}</span>
              {i < PIPELINE.length - 1 && <span className="text-slate-600">→</span>}
            </span>
          ))}
        </div>
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <StepCard step={PIPELINE[1]} job={audit.job} busy={audit.running} onRun={runAudit}>
          <p className="text-xs text-slate-500">Remove duplicados e leads inválidos antes do disparo.</p>
        </StepCard>
        <StepCard step={PIPELINE[2]} job={warmup.job} busy={warmup.running} onRun={runWarmup}>
          <p className="text-xs text-slate-500">Ações leves com jitter. Contas que caírem entram em failover.</p>
        </StepCard>
      </div>
    </div>
  )
}
