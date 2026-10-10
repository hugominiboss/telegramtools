import { useCallback, useEffect, useState } from 'react'
import api from '../api'
import { Badge, Button, Card, Input, Label } from './ui'
import OwnAccountCard, { UpsellModal } from './OwnAccount'

export default function TabOnboarding({ showToast, onGoContas }) {
  const [options, setOptions] = useState(null)
  const [group, setGroup] = useState('')
  const [limit, setLimit] = useState(10)
  const [running, setRunning] = useState(false)
  const [upsell, setUpsell] = useState(null)

  const load = useCallback(async () => {
    try {
      setOptions(await api.onboardingOptions())
    } catch {
      setOptions(null)
    }
  }, [])

  useEffect(() => {
    load()
    const i = setInterval(load, 5000)
    return () => clearInterval(i)
  }, [load])

  const quota = options?.option_a
  const pct = quota?.quota ? Math.min(100, Math.round(((quota.used || 0) / quota.quota) * 100)) : 0

  const runShared = async (e) => {
    e.preventDefault()
    if (!group.trim()) {
      showToast('Informe o link público do grupo.', 'error')
      return
    }
    setRunning(true)
    try {
      const res = await api.sharedExtract({ group: group.trim(), limit: Number(limit) || 10 })
      showToast(`Extração compartilhada #${res.job_id} iniciada!`, 'success')
      load()
    } catch (err) {
      if (err.upgradeRequired) setUpsell(err.payload?.detail || { message: err.message })
      showToast(err.message, 'error')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="!border-emerald-500/40">
          <div className="mb-2 flex items-center justify-between">
            <Badge tone="emerald">Opção A · Risco Zero</Badge>
            {quota && <span className="text-xs tabular-nums text-slate-500">{quota.used}/{quota.quota} usadas</span>}
          </div>
          <h2 className="text-base font-bold text-slate-100">Usar número compartilhado</h2>
          <p className="mt-1 text-xs text-slate-500">Só grupos públicos · até 100 extrações · sem configurar nada.</p>
          {quota && (
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-800">
              <div className={`h-full rounded-full transition-all ${pct >= 90 ? 'bg-rose-500' : 'bg-gradient-to-r from-emerald-400 to-teal-400'}`} style={{ width: `${pct}%` }} />
            </div>
          )}
        </Card>
        <Card>
          <div className="mb-2"><Badge tone="sky">Opção B · Performance Full</Badge></div>
          <h2 className="text-base font-bold text-slate-100">Conectar meu próprio número</h2>
          <p className="mt-1 text-xs text-slate-500">OTP + SessionString · grupos privados · volume ilimitado · failover.</p>
          <div className="mt-3">
            <Button variant="secondary" onClick={onGoContas} className="w-full">Gerenciar minhas contas</Button>
          </div>
        </Card>
      </div>

      <Card>
        <h2 className="mb-1 text-sm font-semibold text-slate-200">Extração Risco Zero</h2>
        <p className="mb-4 text-xs text-slate-500">Apenas links públicos (<code className="text-emerald-400">https://t.me/...</code>). Convites <code>+</code>/<code>joinchat</code> exigem conta própria.</p>
        <form onSubmit={runShared} className="grid gap-4 md:grid-cols-[1fr_140px_auto] md:items-end">
          <div><Label>Grupo público</Label><Input value={group} onChange={(e) => setGroup(e.target.value)} placeholder="https://t.me/duolingo" required /></div>
          <div><Label>Quantidade</Label><Input type="number" min="1" max="100" value={limit} onChange={(e) => setLimit(e.target.value)} /></div>
          <Button type="submit" loading={running} className="h-[42px]">Extrair grátis</Button>
        </form>
      </Card>

      <OwnAccountCard showToast={showToast} onLinked={load} />
      <UpsellModal detail={upsell} onClose={() => setUpsell(null)} onConnectOwn={() => { setUpsell(null); onGoContas?.() }} />
    </div>
  )
}
