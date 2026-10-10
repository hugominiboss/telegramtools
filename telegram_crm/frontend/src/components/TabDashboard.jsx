import { useCallback, useEffect, useState } from 'react'
import api from '../api'
import { Badge, Card, Spinner } from './ui'

function StatCard({ label, value, icon, tone, loading }) {
  const tones = {
    emerald: 'from-emerald-500/20 to-emerald-500/5 text-emerald-400',
    sky: 'from-sky-500/20 to-sky-500/5 text-sky-400',
    amber: 'from-amber-500/20 to-amber-500/5 text-amber-400',
    violet: 'from-violet-500/20 to-violet-500/5 text-violet-400',
  }
  return (
    <Card className="relative overflow-hidden">
      <div
        className={`pointer-events-none absolute -right-6 -top-6 size-24 rounded-full bg-gradient-to-br blur-2xl ${tones[tone]}`}
      />
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-slate-500">{label}</p>
          <p className="mt-2 text-3xl font-bold tabular-nums text-slate-100">
            {loading ? <Spinner className="size-6 text-slate-500" /> : value}
          </p>
        </div>
        <div
          className={`flex size-10 items-center justify-center rounded-xl bg-gradient-to-br text-lg ${tones[tone]}`}
        >
          {icon}
        </div>
      </div>
    </Card>
  )
}

function HealthLed({ health, online }) {
  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
            Monitor de Saúde
          </p>
          <p className="mt-2 text-xl font-bold">{online ? 'Operacional' : 'Indisponível'}</p>
          <p className="mt-1 text-xs text-slate-500">
            Banco de dados:{' '}
            {health?.database ? (
              <span className="text-emerald-400">conectado</span>
            ) : (
              <span className="text-rose-400">sem conexão</span>
            )}
          </p>
        </div>
        <div className="flex flex-col items-center gap-2">
          <span
            className={`size-8 rounded-full border-4 border-slate-800 ${
              online
                ? 'led-pulse bg-emerald-400 shadow-[0_0_25px_rgba(52,211,153,0.7)]'
                : 'bg-rose-500 shadow-[0_0_25px_rgba(244,63,94,0.7)]'
            }`}
          />
          <span
            className={`text-xs font-semibold ${online ? 'text-emerald-400' : 'text-rose-400'}`}
          >
            {online ? 'ONLINE' : 'OFFLINE'}
          </span>
        </div>
      </div>
      <div className="mt-4 border-t border-slate-800 pt-3">
        <p className="text-[11px] text-slate-500">Atualiza automaticamente a cada 5 segundos</p>
      </div>
    </Card>
  )
}

function JobRow({ job }) {
  const pct = job.progress_pct ?? 0
  const tone =
    job.status === 'done'
      ? 'emerald'
      : job.status === 'failed'
        ? 'rose'
        : job.status === 'running'
          ? 'sky'
          : 'slate'
  return (
    <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 px-3.5 py-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge tone={tone}>{job.status}</Badge>
          <span className="text-xs text-slate-500">#{job.id}</span>
          <span className="text-xs text-slate-400">
            {job.job_type === 'harvester' ? '⛏ Extração' : '➤ Disparo'}
          </span>
        </div>
        <span className="text-xs tabular-nums text-slate-400">
          {job.processed}/{job.total} · {pct}%
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            job.status === 'failed'
              ? 'bg-rose-500'
              : job.status === 'done'
                ? 'bg-emerald-500'
                : 'bg-gradient-to-r from-sky-400 to-emerald-400'
          }`}
          style={{ width: `${job.status === 'done' ? 100 : pct}%` }}
        />
      </div>
    </div>
  )
}


export default function TabDashboard({ health, online, showToast }) {
  const [summary, setSummary] = useState(null)
  const [jobs, setJobs] = useState([])
  const [accounts, setAccounts] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const [progress, jobList, acc] = await Promise.all([
        api.progress().catch(() => null),
        api.listJobs(12).catch(() => ({ jobs: [] })),
        api.listAccounts().catch(() => null),
      ])
      setSummary(progress)
      setJobs(jobList?.jobs || [])
      setAccounts(acc)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const i = setInterval(load, 4000)
    return () => clearInterval(i)
  }, [load])

  const leads = summary?.leads || {}
  const totalLeads = summary?.total_leads ?? 0
  const activeAccounts = accounts
    ? `${accounts.accounts.filter((a) => a.status === 'active').length}/${accounts.total}`
    : '—'

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total de Leads" value={totalLeads} icon="👥" tone="emerald" loading={loading} />
        <StatCard label="Contas Ativas" value={activeAccounts} icon="👤" tone="sky" loading={loading} />
        <StatCard label="Mensagens Enviadas" value={leads.sent ?? 0} icon="➤" tone="amber" loading={loading} />
        <StatCard label="Leads Pendentes" value={leads.pending ?? 0} icon="🕓" tone="violet" loading={loading} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <HealthLed health={health} online={online} />

        <Card>
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-200">Distribuição de Leads</p>
            <button
              onClick={() => {
                load()
                showToast('Dados atualizados', 'success')
              }}
              className="text-xs text-slate-500 transition-colors hover:text-emerald-400"
            >
              ↻ Atualizar
            </button>
          </div>
          <div className="space-y-3">
            {[
              { k: 'Pendentes', v: leads.pending ?? 0, c: 'bg-slate-400' },
              { k: 'Enviados', v: leads.sent ?? 0, c: 'bg-emerald-400' },
              { k: 'Falharam', v: leads.failed ?? 0, c: 'bg-rose-400' },
              { k: 'Responderam', v: leads.replied ?? 0, c: 'bg-sky-400' },
            ].map((row) => {
              const pct = totalLeads > 0 ? Math.round((row.v / totalLeads) * 100) : 0
              return (
                <div key={row.k}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="text-slate-400">{row.k}</span>
                    <span className="tabular-nums text-slate-300">
                      {row.v} · {pct}%
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className={`h-full rounded-full transition-all duration-700 ${row.c}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </Card>
      </div>

      <Card>
        <p className="mb-3 text-sm font-semibold text-slate-200">Atividade Recente</p>
        {jobs.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">
            Nenhum job executado ainda. Vá para <b>Extração</b> para começar.
          </p>
        ) : (
          <div className="space-y-2.5">
            {jobs.map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

