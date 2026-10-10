"""Sistema de feedback visual em tempo real — arquivo `.current_status.txt`.

Motivação: o operador não precisa ler o chat do agente. Basta observar o
arquivo `.current_status.txt` na raiz do projeto (ex.: `tail -f .current_status.txt`)
para acompanhar a barra de progresso da execução autônoma.

Formato de cada linha:

    [COR ANSI] | TAREFA: <nome> | % TAREFA: <0-100%>
    | PROGRESSO TOTAL: [▓▓▓░░░ <0-100%>] | TEMPO RESTANTE TOTAL: <X min>

Uso:
    from app.core.status import status
    status.start(total_tasks=24, estimate_seconds=45 * 60)
    status.task("SESSION MANAGER", task_pct=80)   # 80% da tarefa atual
    status.finish()
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

STATUS_FILE = Path(__file__).resolve().parents[2] / ".current_status.txt"

# --------------------------------------------------------------------------
# Cores ANSI (faixas de progresso total)
# --------------------------------------------------------------------------
_ANSI_RED = "\033[91m"
_ANSI_YELLOW = "\033[93m"
_ANSI_CYAN = "\033[96m"
_ANSI_GREEN = "\033[92m"
_ANSI_RESET = "\033[0m"

_FILLED = "▓"
_EMPTY = "░"
_BAR_WIDTH = 6
_MAX_LINES = 1000  # trim defensivo para o arquivo não crescer sem limite


def _color_for(total_pct: int) -> str:
    """Cor ANSI muda conforme o progresso total — feedback visual imediato."""
    if total_pct >= 90:
        return _ANSI_GREEN
    if total_pct >= 60:
        return _ANSI_CYAN
    if total_pct >= 30:
        return _ANSI_YELLOW
    return _ANSI_RED


class StatusReporter:
    """Escreve o estado atual da execução autônoma no `.current_status.txt`."""

    def __init__(self, path: Path = STATUS_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._estimate_seconds: float = 45 * 60
        self._tasks_total = 1
        self._tasks_done = 0
        self._current_task = "AGUARDANDO INÍCIO"
        self._current_task_pct = 0
        self._finished = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def start(self, total_tasks: int, estimate_seconds: float) -> None:
        with self._lock:
            self._started_at = time.monotonic()
            self._estimate_seconds = max(estimate_seconds, 1)
            self._tasks_total = max(total_tasks, 1)
            self._tasks_done = 0
            self._finished = False
            self._current_task = "SETUP DE INFRAESTRUTURA"
            self._current_task_pct = 0
        self.flush()

    def task(self, name: str, task_pct: int = 0) -> None:
        """Atualiza a tarefa corrente (0-100% dentro da tarefa)."""
        with self._lock:
            self._current_task = name
            self._current_task_pct = max(0, min(100, int(task_pct)))
        self.flush()

    def step_done(self, name: str | None = None, task_pct: int = 100) -> None:
        """Marca uma tarefa como concluída e avança o contador global."""
        with self._lock:
            if name is not None:
                self._current_task = name
            self._current_task_pct = max(0, min(100, int(task_pct)))
            if self._tasks_done < self._tasks_total:
                self._tasks_done += 1
        self.flush()

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            self._current_task = "ENTREGA CONCLUÍDA"
            self._current_task_pct = 100
            self._tasks_done = self._tasks_total
        self.flush()

    # ------------------------------------------------------------------
    # Renderização
    # ------------------------------------------------------------------
    @property
    def total_pct(self) -> int:
        if self._finished:
            return 100
        base = int(self._tasks_done * 100 / self._tasks_total)
        partial = int(self._current_task_pct / self._tasks_total)
        return max(0, min(100, base + partial))

    def _eta_minutes(self) -> float:
        if self._finished:
            return 0.0
        elapsed = time.monotonic() - self._started_at
        pct = self.total_pct / 100
        if pct <= 0:
            return round(self._estimate_seconds / 60, 1)
        remaining = (elapsed / pct) - elapsed
        # nunca reporta valor negativo; usa o estimado inicial como teto suave
        return round(max(remaining, 0) / 60, 1)

    def render_line(self) -> str:
        pct = self.total_pct
        color = _color_for(pct)
        filled = round(pct / 100 * _BAR_WIDTH)
        bar = _FILLED * filled + _EMPTY * (_BAR_WIDTH - filled)
        return (
            f"{color}[BOTCASHGAIN]{_ANSI_RESET} | "
            f"TAREFA: {self._current_task} | "
            f"% TAREFA: {self._current_task_pct}% | "
            f"PROGRESSO TOTAL: [{bar} {pct}%] | "
            f"TEMPO RESTANTE TOTAL: {self._eta_minutes()} min"
        )

    def flush(self) -> None:
        """Anexa a linha atual ao arquivo (compatível com `tail -f`)."""
        line = self.render_line()
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                self._trim()
            except OSError:
                # Feedback visual nunca pode derrubar a execução principal.
                pass

    def _trim(self) -> None:
        try:
            if not self.path.exists():
                return
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > _MAX_LINES:
                self.path.write_text(
                    "\n".join(lines[-_MAX_LINES:]) + "\n", encoding="utf-8"
                )
        except OSError:
            pass


# Instância compartilhada por todo o sistema.
status = StatusReporter()

__all__ = ["StatusReporter", "status", "STATUS_FILE"]
