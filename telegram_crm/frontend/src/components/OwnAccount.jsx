import { useState } from 'react'
import api from '../api'
import { Button, Card, Input, Label } from './ui'

function OwnNumberForm({ showToast, onLinked }) {
  const [step, setStep] = useState(1)
  const [form, setForm] = useState({ phone: '', api_id: '', api_hash: '', code: '', phone_code_hash: '', password: '' })
  const [busy, setBusy] = useState(false)
  const update = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const start = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const res = await api.authStart({ phone: form.phone.trim(), api_id: Number(form.api_id), api_hash: form.api_hash.trim() })
      setForm((f) => ({ ...f, phone_code_hash: res.phone_code_hash }))
      setStep(2)
      showToast('Código enviado ao seu Telegram!', 'success')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const complete = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const res = await api.authComplete({
        phone: form.phone.trim(),
        phone_code_hash: form.phone_code_hash,
        code: form.code.trim() || null,
        password: form.password || null,
      })
      showToast(`Conta ${res.phone} conectada com acesso total!`, 'success')
      onLinked?.()
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  if (step === 1) {
    return (
      <form onSubmit={start} className="grid gap-4 md:grid-cols-3">
        <div><Label>Telefone (com DDI)</Label><Input value={form.phone} onChange={update('phone')} placeholder="+5511999999999" required /></div>
        <div><Label>API ID</Label><Input value={form.api_id} onChange={update('api_id')} placeholder="123456" inputMode="numeric" required /></div>
        <div><Label>API Hash</Label><Input value={form.api_hash} onChange={update('api_hash')} placeholder="0123456789abcdef..." required /></div>
        <div className="md:col-span-3">
          <p className="mb-3 text-xs text-slate-500">Pegue api_id/api_hash em <code className="text-sky-400">my.telegram.org</code>. O código chega no seu Telegram.</p>
          <Button type="submit" loading={busy}>Enviar código OTP</Button>
        </div>
      </form>
    )
  }
  return (
    <form onSubmit={complete} className="grid gap-4 md:grid-cols-3">
      <div><Label>Código recebido</Label><Input value={form.code} onChange={update('code')} placeholder="12345" inputMode="numeric" /></div>
      <div><Label hint="opcional">Senha 2FA</Label><Input type="password" value={form.password} onChange={update('password')} placeholder="••••••" /></div>
      <div className="flex items-end gap-2.5">
        <Button type="submit" loading={busy}>Conectar conta</Button>
        <Button variant="secondary" onClick={() => setStep(1)}>Voltar</Button>
      </div>
    </form>
  )
}

export function UpsellModal({ detail, onClose, onConnectOwn }) {
  if (!detail) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-amber-500/30 bg-slate-900 p-6 shadow-2xl">
        <div className="mb-3 text-3xl">🚀</div>
        <h2 className="text-lg font-bold text-slate-100">Limite da conta compartilhada</h2>
        <p className="mt-2 text-sm text-slate-400">{detail.message || 'Teto de 100 extrações atingido.'}</p>
        <div className="mt-4 rounded-lg border border-slate-800 bg-slate-950/60 px-3.5 py-3 text-xs text-slate-400">
          Conecte seu próprio número para <b className="text-emerald-400">acesso total</b>: grupos privados, volume ilimitado e failover automático.
        </div>
        <div className="mt-5 flex gap-2.5">
          <Button onClick={onConnectOwn} className="flex-1">Conectar meu número</Button>
          <Button variant="secondary" onClick={onClose}>Depois</Button>
        </div>
      </div>
    </div>
  )
}

export default function OwnAccountCard({ showToast, onLinked }) {
  return (
    <Card>
      <h2 className="mb-1 text-sm font-semibold text-slate-200">Conectar conta própria (OTP)</h2>
      <p className="mb-4 text-xs text-slate-500">Opção B · Performance Full — acesso total, sem teto.</p>
      <OwnNumberForm showToast={showToast} onLinked={onLinked} />
    </Card>
  )
}
