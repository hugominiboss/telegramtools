import { useEffect, useRef, useState } from 'react'
import api from '../api'
import { Badge, Button, Card, Label } from './ui'

export default function TabDisparo({ showToast }) {
  const [message, setMessage] = useState('')
  const [savingBait, setSavingBait] = useState(false)
  const [sending, setSending] = useState(false)
  const [job, setJob] = useState(null)
  const [lastResult, setLastResult] = useState(null)
  const [pendingCount, setPendingCount] = useState(null)
  const pollRef = useRef(null)

  // Carrega a isca ativa + contagem de pendentes
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const [bait, prog] = await Promise.all([
          api.getBait().catch(() => null),
          api.progress().catch(() => null),
        ])
        if (!alive) return
        if (bait?.message) setMessage(bait.message)
        if (prog?.leads) setPendingCount(prog.leads.pending ?? 0)
      } catch {
        /* ignora */
      }
    }
    load()
    const i = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(i)
      clearInterval(pollRef.current)
    }
  }, [])

  const saveBait = async () => {
    if (!message.trim()) {
      showToast('A mensagem não pode ficar vazia.', 'error')
      return
    }
    setSavingBait(true)
    try {
      await api.setBait(message)
      showToast('Mensagem de isca salva!', 'success')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSavingBait(false)
    }
  }

  const pollJob = (jobId) => {
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const info = await api.getJob(jobId)
        setJob(info)
        if (['done', 'failed', 'cancelled'].includes(info.status)) {
          clearInterval(pollRef.current)
          setSending(false)
          const result = info?.params?.result || {}
          setLastResult(result)
          if (info.status === 'done') {
            showToast(`Disparo concluído: ${result.sent ?? 0} enviados!`, 'success')
          } else {
            showToast(info.error || 'Disparo falhou.', 'error')
          }
        }
      } catch (err) {
        clearInterval(pollRef.current)
        setSending(false)
        showToast(err.message, 'error')
      }
    }, 1500)
  }

  const dispatch = async () => {
    if (!message.trim()) {
      showToast('Escreva e salve a mensagem antes de disparar.', 'error')
      return
    }
    setSending(true)
    setJob(null)
    setLastResult(null)
    try {
      // Salva a isca antes de disparar (garante consistência)
      await api.setBait(message)
      const res = await api.dispatch({ limit: pendingCount || 50 })
      showToast(`Disparo #${res.job_id} iniciado!`, 'info')
      pollJob(res.job_id)
    } catch (err) {
      setSending(false)
      showToast(err.message, 'error')
    }
  }

  const pct = job ? (job.status === 'done' ? 100 : (job.progress_pct ?? 0)) : 0

  return (
    <div className="space-y-5">
      <Card>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">Mensagem de Isca</h2>
          {pendingCount !== null && <Badge tone="violet">{pendingCount} leads pendentes</Badge>}
        </div>
        <p className="mb-3 text-xs text-slate-500">
          Placeholders: <code className="text-emerald-400">{'{first_name}'}</code>,{' '}
          <code className="text-emerald-400">{'{username}'}</code>,{' '}
          <code className="text-emerald-400">{'{last_name}'}</code>
        </p>
        <Label>Texto da abordagem</Label>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={5}
          placeholder="E ai {first_name}! Vi seu perfil no grupo e tenho uma dica rápida pra você..."
          className="w-full resize-y rounded-lg border border-slate-800 bg-slate-900/70 px-3.5 py-3 text-sm text-slate-100 placeholder:text-slate-500 outline-none transition-colors focus:border-emerald-500/60 focus:ring-2 focus:ring-emerald-500/15"
        />
        <div className="mt-3 flex gap-2.5">
          <Button variant="secondary" onClick={saveBait} loading={savingBait}>
            Salvar isca
          </Button>
          <Button onClick={dispatch} loading={sending}>
            {sending ? 'Enviando...' : 'Disparar Agora'}
          </Button>
        </div>
      </Card>

      {job && (
        <Card>
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-200">
              {job.status === 'done' ? 'Disparo concluído' : job.status === 'failed' ? 'Disparo falhou' : 'Enviando...'}
            </p>
            <Badge tone={job.status === 'done' ? 'emerald' : job.status === 'failed' ? 'rose' : 'sky'}>
              {job.status} · {pct}%
            </Badge>
          </div>
          <div className="h-2.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className={`h-full rounded-full transition-all duration-700 ${job.status === 'failed' ? 'bg-rose-500' : 'bg-gradient-to-r from-emerald-400 to-teal-400'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="mt-2 text-xs tabular-nums text-slate-500">
            {job.processed ?? 0}/{job.total ?? 0} processados
            {lastResult && <> · {lastResult.sent ?? 0} enviados · {lastResult.failed ?? 0} falharam</>}
          </p>
          {job.error && (
            <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-300">
              {job.error}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

