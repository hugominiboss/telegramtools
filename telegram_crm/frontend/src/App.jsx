import { useCallback, useEffect, useState } from 'react'
import api from './api'
import { Card, Toast } from './components/ui'
import { Crosshair, Database, Flame, LayoutDashboard, Rocket, Send, UserPlus, Users } from 'lucide-react'
import TabDashboard from './components/TabDashboard'
import TabContas from './components/TabContas'
import TabExtracao from './components/TabExtracao'
import TabDisparo from './components/TabDisparo'
import TabMembroAdder from './components/TabMembroAdder'
import TabPipeline from './components/TabPipeline'
import TabInfra from './components/TabInfra'
import TabOnboarding from './components/TabOnboarding'

const TABS = [
  { id: 'dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { id: 'onboarding', label: 'Começar', Icon: Rocket },
  { id: 'contas', label: 'Contas', Icon: Users },
  { id: 'extracao', label: 'Scraper', Icon: Crosshair },
  { id: 'pipeline', label: 'Audit · Warmup', Icon: Flame },
  { id: 'membroadder', label: 'Adicionar', Icon: UserPlus },
  { id: 'disparo', label: 'Conversion', Icon: Send },
  { id: 'infra', label: 'Infra', Icon: Database },
]

const DESCRIPTIONS = {
  dashboard: 'Visão geral do sistema em tempo real.',
  onboarding: 'Degraus de confiança: comece grátis ou conecte sua conta.',
  contas: 'Vincule SessionStrings das contas GGMax e monitore o status.',
  extracao: 'Scraper: extraia leads de qualquer grupo automaticamente.',
  pipeline: 'Audit valida a base · Warmup aquece as contas anti-ban.',
  membroadder: 'Adicione forçadamente os leads auditados a um grupo seu (seja admin).',
  disparo: 'Conversion: configure a isca e dispare para os leads.',
  infra: 'Banco, tabelas BotCashGain, contas e proxies.',
}

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [health, setHealth] = useState(null)
  const [toast, setToast] = useState(null)

  const showToast = useCallback((message, type = 'info') => {
    setToast({ message, type, id: Date.now() })
  }, [])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(t)
  }, [toast])

  // Monitor de saúde: polling a cada 5s
  useEffect(() => {
    let alive = true
    const check = async () => {
      try {
        const h = await api.health()
        if (alive) setHealth(h)
      } catch {
        if (alive) setHealth(null)
      }
    }
    check()
    const i = setInterval(check, 5000)
    return () => {
      alive = false
      clearInterval(i)
    }
  }, [])

  const online = health?.status === 'ok'

  return (
    <div className="min-h-screen text-slate-100">
      {/* Fundo decorativo */}
      <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
        <div className="absolute -top-40 left-1/2 h-[420px] w-[820px] -translate-x-1/2 rounded-full bg-emerald-500/10 blur-[130px]" />
        <div className="absolute -bottom-40 -left-40 h-[360px] w-[360px] rounded-full bg-sky-500/10 blur-[120px]" />
      </div>

      <div className="mx-auto flex min-h-screen max-w-[1400px] gap-6 px-5 py-6">
        {/* ---------------- Sidebar ---------------- */}
        <aside className="hidden w-60 shrink-0 flex-col lg:flex">
          <div className="mb-8 flex items-center gap-3 px-2">
            <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 text-lg font-black text-slate-950 shadow-lg shadow-emerald-500/25">
              ⚡
            </div>
            <div>
              <div className="text-[15px] font-bold leading-tight">BotCashGain</div>
              <div className="text-[11px] text-slate-500">Painel de Controle</div>
            </div>
          </div>

          <nav className="flex flex-col gap-1.5">
            {TABS.map(({ id, label, Icon }) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-all ${
                  tab === id
                    ? 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30'
                    : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
                }`}
              >
                <Icon className="size-4 shrink-0" />
                {label}
              </button>
            ))}
          </nav>

          <div className="mt-auto">
            <Card className="!p-4">
              <div className="mb-2 flex items-center gap-2">
                <span
                  className={`size-2.5 rounded-full ${
                    online ? 'led-pulse bg-emerald-400' : 'bg-rose-500'
                  }`}
                />
                <span className="text-xs font-semibold text-slate-300">
                  {online ? 'Servidor online' : 'Servidor offline'}
                </span>
              </div>
              <p className="text-[11px] text-slate-500">{api.url}</p>
              {health && (
                <p className="mt-1 text-[11px] text-slate-500">
                  Banco: {health.database ? 'conectado' : 'indisponível'}
                </p>
              )}
            </Card>
          </div>
        </aside>

        {/* ---------------- Conteúdo ---------------- */}
        <main className="min-w-0 flex-1">
          {/* Header mobile */}
          <div className="mb-5 flex items-center justify-between lg:hidden">
            <div className="flex items-center gap-2.5">
              <div className="flex size-9 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-teal-600 text-base font-black text-slate-950">
                ⚡
              </div>
              <span className="font-bold">BotCashGain</span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={`size-2.5 rounded-full ${
                  online ? 'led-pulse bg-emerald-400' : 'bg-rose-500'
                }`}
              />
              <span className="text-xs text-slate-400">{online ? 'Online' : 'Offline'}</span>
            </div>
          </div>

          {/* Tabs mobile */}
          <div className="mb-5 flex gap-2 overflow-x-auto pb-1 lg:hidden">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`shrink-0 rounded-lg px-3.5 py-2 text-sm font-medium ${
                  tab === t.id
                    ? 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30'
                    : 'bg-slate-800/50 text-slate-400'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <header className="mb-6">
            <h1 className="text-2xl font-bold tracking-tight">
              {TABS.find((t) => t.id === tab)?.label}
            </h1>
            <p className="mt-1 text-sm text-slate-500">{DESCRIPTIONS[tab]}</p>
          </header>

          {tab === 'dashboard' && (
            <TabDashboard health={health} online={online} showToast={showToast} />
          )}
          {tab === 'onboarding' && (
            <TabOnboarding showToast={showToast} onGoContas={() => setTab('contas')} />
          )}
          {tab === 'contas' && <TabContas showToast={showToast} />}
          {tab === 'extracao' && <TabExtracao showToast={showToast} />}
          {tab === 'pipeline' && <TabPipeline showToast={showToast} />}
          {tab === 'membroadder' && <TabMembroAdder showToast={showToast} />}
          {tab === 'disparo' && <TabDisparo showToast={showToast} />}
          {tab === 'infra' && <TabInfra showToast={showToast} />}
        </main>
      </div>

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  )
}

