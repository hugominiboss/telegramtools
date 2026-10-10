import { useCallback, useEffect, useState } from 'react'
import api from '../api'
import { Badge, Button, Card, EmptyState, Input, Label } from './ui'

const STATUS_TONE = { active: 'emerald', banned: 'rose', limited: 'amber' }
const STATUS_LABEL = { active: 'Ativa', banned: 'Banida', limited: 'Limitada' }

export default function TabContas({ showToast }) {
  const [accounts, setAccounts] = useState({ total: 0, accounts: [] })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const [form, setForm] = useState({
    phone: '',
    api_id: '',
    api_hash: '',
    session_string: '',
    proxy: '',
  })

  const load = useCallback(async () => {
    try {
      const data = await api.listAccounts()
      setAccounts(data)
    } catch (e) {
      showToast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => {
    load()
  }, [load])

  const update = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    if (!form.phone || !form.api_id || !form.api_hash || !form.session_string) {
      showToast('Preencha phone, api_id, api_hash e session_string.', 'error')
      return
    }
    setSaving(true)
    try {
      const res = await api.createSession({
        phone: form.phone.trim(),
        api_id: Number(form.api_id),
        api_hash: form.api_hash.trim(),
        session_string: form.session_string.trim(),
        proxy: form.proxy.trim() || null,
      })
      showToast(
        res.created ? `Conta ${res.phone} vinculada!` : `Conta ${res.phone} atualizada!`,
        'success'
      )
      setForm({ phone: '', api_id: '', api_hash: '', session_string: '', proxy: '' })
      load()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggleStatus = async (acc) => {
    const next = acc.status === 'active' ? 'banned' : 'active'
    try {
      await api.updateAccountStatus(acc.id, next)
      showToast(`Conta ${acc.phone} → ${STATUS_LABEL[next]}`, 'success')
      load()
    } catch (err) {
      showToast(err.message, 'error')
    }
  }

  return (
    <div className="space-y-5">
      {/* Formulário de vínculo */}
      <Card>
        <h2 className="mb-1 text-sm font-semibold text-slate-200">Vincular Nova Conta</h2>
        <p className="mb-4 text-xs text-slate-500">
          Cole a StringSession gerada pela GGMax. O proxy é atribuído automaticamente do pool
          rotativo caso deixe em branco.
        </p>

        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
          <div>
            <Label>Telefone (com DDI)</Label>
            <Input
              value={form.phone}
              onChange={update('phone')}
              placeholder="+5511999999999"
              required
            />
          </div>
          <div>
            <Label>API ID</Label>
            <Input
              value={form.api_id}
              onChange={update('api_id')}
              placeholder="123456"
              inputMode="numeric"
              required
            />
          </div>
          <div>
            <Label>API Hash</Label>
            <Input
              value={form.api_hash}
              onChange={update('api_hash')}
              placeholder="0123456789abcdef..."
              required
            />
          </div>
          <div>
            <Label hint="opcional">Proxy</Label>
            <Input
              value={form.proxy}
              onChange={update('proxy')}
              placeholder="socks5://user:pass@host:port"
            />
          </div>
          <div className="md:col-span-2">
            <Label>Session String</Label>
            <textarea
              value={form.session_string}
              onChange={update('session_string')}
              rows={3}
              placeholder="1BQANOTEuMTA4LjU2LjE0MAG7..."
              className="w-full resize-y rounded-lg border border-slate-800 bg-slate-900/70 px-3.5 py-3 font-mono text-xs text-slate-100 placeholder:text-slate-500 outline-none transition-colors focus:border-emerald-500/60 focus:ring-2 focus:ring-emerald-500/15"
              required
            />
          </div>
          <div className="md:col-span-2">
            <Button type="submit" loading={saving} className="w-full md:w-auto">
              {saving ? 'Vinculando...' : 'Vincular Conta'}
            </Button>
          </div>
        </form>
      </Card>


      {/* Tabela de contas */}
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">Contas Cadastradas</h2>
          <Button variant="secondary" onClick={load} className="!px-3 !py-1.5 !text-xs">
            ↻ Atualizar
          </Button>
        </div>

        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="shimmer h-12 rounded-lg" />
            ))}
          </div>
        ) : accounts.accounts.length === 0 ? (
          <EmptyState
            title="Nenhuma conta vinculada"
            description="Cole uma SessionString acima para começar a operar."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
                  <th className="py-2.5 pr-4">ID</th>
                  <th className="py-2.5 pr-4">Telefone</th>
                  <th className="py-2.5 pr-4">Status</th>
                  <th className="py-2.5 pr-4">Proxy</th>
                  <th className="py-2.5 text-right">Ação</th>
                </tr>
              </thead>
              <tbody>
                {accounts.accounts.map((acc) => (
                  <tr
                    key={acc.id}
                    className="border-b border-slate-800/50 transition-colors hover:bg-slate-800/20"
                  >
                    <td className="py-3 pr-4 tabular-nums text-slate-400">#{acc.id}</td>
                    <td className="py-3 pr-4 font-medium text-slate-200">{acc.phone}</td>
                    <td className="py-3 pr-4">
                      <Badge tone={STATUS_TONE[acc.status] || 'slate'}>
                        {STATUS_LABEL[acc.status] || acc.status}
                      </Badge>
                    </td>
                    <td className="max-w-[220px] truncate py-3 pr-4 font-mono text-xs text-slate-500">
                      {acc.proxy || '—'}
                    </td>
                    <td className="py-3 text-right">
                      <button
                        onClick={() => toggleStatus(acc)}
                        className="text-xs text-slate-500 transition-colors hover:text-emerald-400"
                      >
                        {acc.status === 'active' ? 'Marcar Banida' : 'Reativar'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

