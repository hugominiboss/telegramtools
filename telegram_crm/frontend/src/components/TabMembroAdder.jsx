import { useEffect, useRef, useState } from 'react'
import api from '../api'
import { Badge, Button, Card, Input, Label } from './ui'

export default function TabMembroAdder({ showToast }) {
  const [group, setGroup] = useState('')
  const [limit, setLimit] = useState(10)
  const [running, setRunning] = useState(false)
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  useEffect(() => () => clearInterval(pollRef.current), [])

  const pollJob = (jobId) => {
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const info = await api.getJob(jobId)
        setJob(info)
        if (['done', 'failed', 'cancelled'].includes(info.status)) {
          clearInterval(pollRef.current)
          setRunning(false)
          const res = info?.params?.result || {}
          if (info.status === 'done') {
            showToast(`Adição concluída: ${res.added ?? 0} membros adicionados!`, 'success')
          } else {
            showToast(info.error || 'Adição falhou.', 'error')
          }
        }
      } catch (err) {
        clearInterval(pollRef.current)
        setRunning(false)
        showToast(err.message, 'error')
      }
    }, 1500)
  }

  const start = async (e) => {
    e.preventDefault()
    if (!group.trim()) {
      showToast('Informe o link ou id do grupo-alvo.', 'error')
      return
    }
    setRunning(true)
    setError(null)
    setJob(null)
    try {
      const res = await api.startAdder({
        target_group: group.trim(),
        limit: Number(limit) || 10,
      })
      showToast(`Adição #${res.job_id} iniciada!`, 'success')
      pollJob(res.job_id)
    } catch (err) {
      setRunning(false)
      setError(err.message)
      showToast(err.message, 'error')
    }
  }

  const pct = job ? (job.status === 'done' ? 100 : (job.progress_pct ?? 0)) : 0
  const res = job?.params?.result || {}
  const processed = job?.processed ?? 0
  const total = job?.total ?? limit
  const failed = job?.failed ?? res.failed ?? 0
  const added = res.added ?? 0

  return (
    <div className="space-y-5">
      <Card>
        <h2 className="mb-1 text-sm font-semibold text-slate-200">Adicionar Membros (Member Adder)</h2>
        <p className="mb-4 text-xs text-slate-500">
          Adiciona os leads já extraídos e auditados a um grupo/canal. Use a conta como
          <b className="text-slate-300"> administrador</b> do grupo-alvo. Os convites respeitam
          intervalos humanos e fazem failover automático entre contas para evitar banimentos.
        </p>

        <form onSubmit={start} className="grid gap-4 md:grid-cols-[1fr_160px_auto] md:items-end">
          <div>
            <Label>Grupo-alvo (você deve ser admin)</Label>
            <Input
              value={group}
              onChange={(e) => setGroup(e.target.value)}
              placeholder="https://t.me/s/meu_grupo ou -1001234567890"
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
            {running ? 'Adicionando...' : 'Iniciar Adição'}
          </Button>
        </form>
      </Card>

      {job && (
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-slate-200">
                {job.status === 'done'
                  ? 'Adição concluída'
                  : job.status === 'failed'
                    ? 'Adição falhou'
                    : 'Adicionando membros...'}
              </p>
              <p className="text-xs text-slate-500">{group}</p>
            </div>
            <Badge tone={job.status === 'done' ? 'emerald' : job.status === 'failed' ? 'rose' : 'sky'}>
              {job.status || 'running'}
            </Badge>
          </div>

          <div className="mb-4 flex items-end gap-5">
            <div>
              <span className="text-4xl font-black leading-none tabular-nums text-emerald-400">
                {added}
              </span>
              <span className="ml-1.5 text-sm text-slate-400">adicionados</span>
            </div>
            <div>
              <span className="text-2xl font-bold leading-none tabular-nums text-rose-400">
                {failed}
              </span>
              <span className="ml-1.5 text-xs text-slate-500">falharam</span>
            </div>
          </div>

          <div className="h-2.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className={`h-full rounded-full transition-all duration-700 ${
                job.status === 'failed' ? 'bg-rose-500' : 'bg-gradient-to-r from-emerald-400 to-teal-400'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="mt-2 flex justify-between text-xs tabular-nums text-slate-500">
            <span>{processed}/{total} processados</span>
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
    </div>
  )
}
