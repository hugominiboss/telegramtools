import { useEffect, useRef, useState } from 'react'
import api from '../api'
import { Badge, Button, Card, Input, Label } from './ui'

export default function TabExtracao({ showToast }) {
  const [group, setGroup] = useState('')
  const [limit, setLimit] = useState(50)
  const [running, setRunning] = useState(false)
  const [job, setJob] = useState(null)
  const [leads, setLeads] = useState([])
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  useEffect(() => () => clearInterval(pollRef.current), [])

  const pollJob = (jobId) => {
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const info = await api.getJob(jobId)
        setJob(info)
        const resultLeads = info?.params?.result?.leads || []
        if (resultLeads.length) setLeads(resultLeads)
        if (['done', 'failed', 'cancelled'].includes(info.status)) {
          clearInterval(pollRef.current)
          setRunning(false)
          if (info.status === 'done') {
            showToast(`Extração concluída: ${info.processed} leads!`, 'success')
          } else {
            showToast(info.error || 'Extração falhou.', 'error')
          }
        }
      } catch (err) {
        clearInterval(pollRef.current)
        setRunning(false)
        showToast(err.message, 'error')
      }
    }, 1500)
  }

  const startExtraction = async (e) => {
    e.preventDefault()
    if (!group.trim()) {
      showToast('Informe o link ou id do grupo.', 'error')
      return
    }
    setRunning(true)
    setError(null)
    setJob(null)
    setLeads([])
    try {
      const res = await api.testExtraction({
        group: group.trim(),
        limit: Number(limit) || 10,
        wait: false,
      })
      showToast(`Extração #${res.job_id} iniciada!`, 'success')
      pollJob(res.job_id)
    } catch (err) {
      setRunning(false)
      setError(err.message)
      showToast(err.message, 'error')
    }
  }

  const pct = job ? (job.status === 'done' ? 100 : (job.progress_pct ?? 0)) : 0
  const captured = job?.processed ?? leads.length

  return (
    <div className="space-y-5">
      <Card>
        <h2 className="mb-1 text-sm font-semibold text-slate-200">Nova Extração</h2>
        <p className="mb-4 text-xs text-slate-500">
          Informe o link público do grupo (ex.:{' '}
          <code className="text-emerald-400">https://t.me/s/grupo</code>) ou o ID numérico.
        </p>

        <form
          onSubmit={startExtraction}
          className="grid gap-4 md:grid-cols-[1fr_160px_auto] md:items-end"
        >
          <div>
            <Label>Grupo alvo</Label>
            <Input
              value={group}
              onChange={(e) => setGroup(e.target.value)}
              placeholder="https://t.me/s/meu_grupo"
              required
            />
          </div>
          <div>
            <Label>Quantidade</Label>
            <Input
              type="number"
              min="1"
              max="5000"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </div>
          <Button type="submit" loading={running} className="h-[42px]">
            {running ? 'Extraindo...' : 'Iniciar Extração'}
          </Button>
        </form>
      </Card>


      {/* Card de progresso */}
      {(running || job) && (
        <Card>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <span className="text-lg">⛏</span>
              <div>
                <p className="text-sm font-semibold text-slate-200">
                  {job?.status === 'done'
                    ? 'Extração concluída'
                    : job?.status === 'failed'
                      ? 'Extração falhou'
                      : 'Extraindo leads...'}
                </p>
                <p className="text-xs text-slate-500">{group}</p>
              </div>
            </div>
            <Badge
              tone={
                job?.status === 'done' ? 'emerald' : job?.status === 'failed' ? 'rose' : 'sky'
              }
            >
              {job?.status || 'running'}
            </Badge>
          </div>

          {/* Número grande de leads capturados */}
          <div className="mb-4 flex items-end gap-3">
            <span className="text-5xl font-black leading-none tabular-nums text-emerald-400">
              {captured}
            </span>
            <span className="mb-1.5 text-sm text-slate-400">
              leads capturados{job?.total ? ` de ${job.total} solicitados` : ''}
            </span>
          </div>

          <div className="h-2.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className={`h-full rounded-full transition-all duration-700 ${
                job?.status === 'failed'
                  ? 'bg-rose-500'
                  : 'bg-gradient-to-r from-emerald-400 to-teal-400'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="mt-2 flex justify-between text-xs tabular-nums text-slate-500">
            <span>
              {job?.processed ?? 0}/{job?.total ?? limit} processados
            </span>
            <span>{pct}%</span>
          </div>

          {job?.error && (
            <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-300">
              {job.error}
            </div>
          )}
        </Card>
      )}

      {error && !job && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}


      {/* Lista de leads capturados */}
      {leads.length > 0 && (
        <Card>
          <p className="mb-3 text-sm font-semibold text-slate-200">
            Leads Capturados <span className="text-slate-500">({leads.length})</span>
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {leads.map((l) => (
              <div
                key={l.user_id}
                className="flex items-center gap-3 rounded-lg border border-slate-800/80 bg-slate-900/40 px-3 py-2.5"
              >
                <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-xs font-bold text-emerald-400">
                  {(l.first_name || l.username || '?').slice(0, 1).toUpperCase()}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-200">
                    {l.first_name || 'Sem nome'}
                  </p>
                  <p className="truncate text-xs text-slate-500">
                    {l.username ? `@${l.username}` : `ID ${l.user_id}`}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

