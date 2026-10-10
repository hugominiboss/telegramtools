import { useCallback, useEffect, useRef, useState } from 'react'
import api from '../api'

/** Polling de job com barra de progresso real (GET /admin/jobs/{id} a cada 1.5s). */
export function useJobPoll(showToast) {
  const [job, setJob] = useState(null)
  const [running, setRunning] = useState(false)
  const timer = useRef(null)

  const stop = useCallback(() => {
    clearInterval(timer.current)
    timer.current = null
  }, [])

  useEffect(() => () => clearInterval(timer.current), [])

  const watch = useCallback((jobId, label = 'Job') => {
    clearInterval(timer.current)
    setRunning(true)
    setJob(null)
    timer.current = setInterval(async () => {
      try {
        const info = await api.getJob(jobId)
        setJob(info)
        if (['done', 'failed', 'cancelled'].includes(info.status)) {
          clearInterval(timer.current)
          setRunning(false)
          if (info.status === 'done') showToast?.(`${label} #${jobId} concluído!`, 'success')
          else showToast?.(info.error || `${label} falhou.`, 'error')
        }
      } catch (err) {
        clearInterval(timer.current)
        setRunning(false)
        showToast?.(err.message, 'error')
      }
    }, 1500)
  }, [showToast])

  return { job, running, watch, stop, setJob }
}

export default useJobPoll
