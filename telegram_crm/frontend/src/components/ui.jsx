/** Primitivos de UI do painel (dark SaaS). */

export function Spinner({ className = 'size-4' }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  )
}

export function Button({
  children,
  onClick,
  loading = false,
  disabled = false,
  variant = 'primary',
  className = '',
  type = 'button',
}) {
  const variants = {
    primary:
      'bg-gradient-to-r from-emerald-500 to-teal-500 text-slate-950 hover:from-emerald-400 hover:to-teal-400 shadow-lg shadow-emerald-500/20',
    secondary:
      'bg-slate-800 text-slate-200 hover:bg-slate-700 border border-slate-700',
    danger:
      'bg-rose-500/10 text-rose-400 border border-rose-500/30 hover:bg-rose-500/20',
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-50 active:scale-[0.98] ${variants[variant]} ${className}`}
    >
      {loading && <Spinner />}
      {children}
    </button>
  )
}

export function Input({ className = '', ...props }) {
  return (
    <input
      {...props}
      className={`w-full rounded-lg border border-slate-800 bg-slate-900/70 px-3.5 py-2.5 text-sm text-slate-100 placeholder:text-slate-500 outline-none transition-colors focus:border-emerald-500/60 focus:ring-2 focus:ring-emerald-500/15 ${className}`}
    />
  )
}

export function Textarea({ className = '', ...props }) {
  return (
    <textarea
      {...props}
      className={`w-full resize-y rounded-lg border border-slate-800 bg-slate-900/70 px-3.5 py-3 text-sm text-slate-100 placeholder:text-slate-500 outline-none transition-colors focus:border-emerald-500/60 focus:ring-2 focus:ring-emerald-500/15 ${className}`}
    />
  )
}

export function Label({ children, hint }) {
  return (
    <div className="mb-2 flex items-baseline justify-between">
      <label className="text-sm font-medium text-slate-300">{children}</label>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </div>
  )
}

export function Card({ children, className = '' }) {
  return (
    <div
      className={`rounded-2xl border border-slate-800/80 bg-slate-900/40 p-5 shadow-xl shadow-black/20 ${className}`}
    >
      {children}
    </div>
  )
}

export function Badge({ children, tone = 'slate' }) {
  const tones = {
    emerald: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
    rose: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
    amber: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
    sky: 'bg-sky-500/10 text-sky-400 border-sky-500/30',
    slate: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${tones[tone] || tones.slate}`}
    >
      {children}
    </span>
  )
}

/** Toast simples ancorado no canto inferior direito. */
export function Toast({ toast, onClose }) {
  if (!toast) return null
  const tones = {
    success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    error: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  }
  return (
    <div className="fixed bottom-5 right-5 z-50 w-[min(380px,calc(100vw-2.5rem))]">
      <div
        className={`flex items-start gap-3 rounded-xl border px-4 py-3 text-sm shadow-2xl backdrop-blur ${tones[toast.type] || tones.info}`}
      >
        <div className="flex-1">{toast.message}</div>
        <button
          onClick={onClose}
          className="shrink-0 text-current/60 transition-colors hover:text-current"
        >
          ✕
        </button>
      </div>
    </div>
  )
}

export function EmptyState({ title, description }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-800 py-12 text-center">
      <div className="mb-3 text-3xl">📭</div>
      <p className="text-sm font-medium text-slate-300">{title}</p>
      {description && <p className="mt-1 text-xs text-slate-500">{description}</p>}
    </div>
  )
}
