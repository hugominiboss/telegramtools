import { useCallback, useEffect, useState } from 'react'
import api from '../api'
import { Badge, Button, Card } from './ui'

export default function TabInfra({ showToast }) {
  const [infra, setInfra] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      setInfra(await api.infra())
    } catch (e) {
      showToast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => {
    load()
    const i = setInterval(load, 8000)
    return () => clearInterval(i)
  }, [load])

  const req = infra?.database?.required || {}
  const allTables = ['accounts', 'sessions', 'leads', 'jobs'].every((t) => req[t])

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <p className="text-xs font-medium uppercase tracking-wider text-slate-500">Banco de dados</p>
          <p className="mt-2 font-mono text-xs text-slate-300">{infra?.database?.url || '—'}</p>
          <div className="mt-3">
            <Badge tone={allTables ? 'emerald' : 'rose'}>
              {loading ? 'checando...' : allTables ? 'tabelas OK' : 'tabelas faltando'}
            </Badge>
          </div>
        </Card>
        <Card>
          <p className="text-xs font-medium uppercase tracking-wider text-slate-500">Contas</p>
          <p className="mt-2 text-3xl font-bold tabular-nums">{infra?.accounts?.active ?? '—'}<span className="text-base text-slate-500">/{infra?.accounts?.total ?? '—'}</span></p>
          <p className="mt-1 text-xs text-slate-500">ativas / total</p>
        </Card>
        <Card>
          <p className="text-xs font-medium uppercase tracking-wider text-slate-500">Proxies</p>
          <p className="mt-2 text-3xl font-bold tabular-nums">{infra?.proxies?.total ?? '—'}</p>
          <p className="mt-1 text-xs text-slate-500">pool SOCKS5 rotativo</p>
        </Card>
      </div>
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">Tabelas BotCashGain</h2>
          <Button variant="secondary" onClick={load} className="!px-3 !py-1.5 !text-xs">↻ Atualizar</Button>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {['accounts', 'sessions', 'leads', 'jobs'].map((t) => (
            <div key={t} className="flex items-center justify-between rounded-lg border border-slate-800/80 bg-slate-900/40 px-3.5 py-3">
              <code className="font-mono text-sm text-slate-200">{t}</code>
              <Badge tone={req[t] ? 'emerald' : 'rose'}>{req[t] ? 'existe' : 'falta'}</Badge>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[11px] text-slate-500">Escopo restrito: o provisionamento só cria tabelas do projeto (create_all). Nenhuma outra tabela é alterada.</p>
      </Card>
    </div>
  )
}
