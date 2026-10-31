# SPDX-License-Identifier: GPL-3.0-or-later
"""Small API budget settings dialog; credential entry is transient and masked."""
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk

from ..ai.openai_budget import BudgetSnapshot, validate_budget
from ..config import OpenAIBudgetConfig
from .app_icon import set_window_icon
from .setup import BG, _window_bounds, _set_window_geometry


class OpenAIBudgetDialog:
    def __init__(self, parent, *, on_save, on_refresh, checkbutton_style="TCheckbutton"):
        self._on_save, self._on_refresh = on_save, on_refresh
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("OpenAI API 사용량 설정")
        self.window.configure(background=BG)
        self.window.transient(parent)
        self.window.resizable(True, True)
        set_window_icon(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Escape>", lambda event: self.hide())
        self.enabled = tk.BooleanVar(self.window, False)
        self.total = tk.StringVar(self.window)
        self.start = tk.StringVar(self.window)
        self.key = tk.StringVar(self.window)
        self.feedback = tk.StringVar(self.window)
        self.summary = tk.StringVar(self.window, "조회 전")
        self.key_status = tk.StringVar(self.window)
        frame = ttk.Frame(self.window, style="Card.TFrame", padding=14)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.columnconfigure(1, weight=1)
        self.enabled_check = ttk.Checkbutton(frame, text="OpenAI API 사용률 표시", variable=self.enabled, style=checkbutton_style)
        self.enabled_check.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="월 총액 (USD)", style="CardText.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.total, width=18).grid(row=1, column=1, sticky="ew", pady=(10, 0))
        ttk.Label(frame, text="시작일 (UTC)", style="CardText.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.start, width=18).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(frame, text="시작일 기준 매월 00:00 UTC에 새 집계 · 조직 전체 비용\n월 총액 유지 · 없는 날짜는 말일 적용 · 잔액 이월 없음",
                  style="CardText.TLabel", wraplength=440).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 12))
        ttk.Label(frame, text="관리자 API 키", style="CardText.TLabel").grid(row=4, column=0, sticky="w")
        self.key_entry = ttk.Entry(frame, textvariable=self.key, show="●", width=30)
        self.key_entry.grid(row=4, column=1, sticky="ew")
        ttk.Label(frame, textvariable=self.key_status, style="CardText.TLabel", wraplength=440).grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Label(frame, text="일반 프로젝트 키가 아닌 조직 Admin API 키가 필요합니다.\n키는 Windows 암호화 저장소에만 저장합니다.",
                  style="CardText.TLabel", wraplength=440).grid(row=6, column=0, columnspan=2, sticky="w", pady=(5, 10))
        ttk.Label(frame, textvariable=self.summary, style="CardTitle.TLabel", wraplength=440, justify="left").grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="설정 총액 대비 사용률입니다. 실제 충전 잔액이 아닙니다.\n자동 갱신 10분 · 공식 비용 집계에 지연이 있을 수 있습니다.",
                  style="CardText.TLabel", wraplength=440).grid(row=8, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Label(frame, textvariable=self.feedback, style="CardText.TLabel", wraplength=440).grid(row=9, column=0, columnspan=2, sticky="w", pady=8)
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.grid(row=10, column=0, columnspan=2, sticky="ew")
        for col in range(3): actions.columnconfigure(col, weight=1)
        ttk.Button(actions, text="저장", style="Primary.TButton", command=self.save).grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="사용금액 갱신", style="Secondary.TButton", command=self.refresh).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(actions, text="닫기", style="Secondary.TButton", command=self.hide).grid(row=0, column=2, sticky="ew")
        frame.bind("<Configure>", lambda event: self._wrap(frame))

    def _wrap(self, frame):
        width = max(80, frame.winfo_width() - 28)
        for child in frame.winfo_children():
            if isinstance(child, ttk.Label) and int(child.cget("wraplength") or 0) > 0:
                child.configure(wraplength=width)

    def show(self, config: OpenAIBudgetConfig, *, key_configured: bool) -> None:
        self.enabled.set(config.enabled)
        self.total.set(config.total_usd)
        self.start.set(config.start_date or datetime.now(timezone.utc).date().isoformat())
        self.key.set("")
        self.feedback.set("")
        self.key_status.set("저장된 키 사용 · 비워 두면 기존 키 유지" if key_configured else "저장된 관리자 키 없음")
        self.window.update_idletasks()
        bounds = _window_bounds(self.window)
        width = min(510, bounds.width - bounds.frame_width)
        height = min(max(480, self.window.winfo_reqheight()), bounds.height - bounds.frame_height)
        _set_window_geometry(self.window, width, height, bounds.left + max(0, (bounds.width - width)//2), bounds.top + max(0, (bounds.height - height)//2))
        self.window.deiconify()
        self.window.lift()

    def hide(self) -> None:
        self.key.set("")
        self.window.withdraw()

    def save(self) -> None:
        config = OpenAIBudgetConfig(self.enabled.get(), self.total.get().strip(), self.start.get().strip())
        if not config.enabled and not config.total_usd:
            config.start_date = ""
        try:
            validate_budget(config)
        except ValueError as error:
            self.feedback.set(str(error))  # validator messages never contain inputs
            return
        try:
            self._on_save(config, self.key.get().strip())
        except Exception:
            self.feedback.set("저장 실패: 관리자 키 입력과 설정 파일 접근 권한을 확인하세요.")
            return
        finally:
            self.key.set("")
        self.feedback.set("저장했습니다. 표시 켜짐 상태에서는 사용금액을 자동 조회합니다.")
        self.key_status.set("키를 다시 입력하지 않으면 기존 키를 유지합니다.")

    def refresh(self) -> None:
        admitted = self._on_refresh()
        self.feedback.set("조회 요청됨" if admitted else "표시를 켜고 저장하거나, 마지막 조회 후 60초가 지난 뒤 시도하세요.")

    def update(self, snapshot: BudgetSnapshot) -> None:
        ai = snapshot.ai
        if ai is None:
            self.summary.set("OpenAI API 표시 꺼짐")
            return
        message = {"pending": "조회 대기", "key_required": "관리자 API 키 입력 필요", "auth": "관리자 키 또는 조직 조회 권한 확인 필요", "rate_limited": "요청 제한 · 다음 주기에 재시도", "network": "연결 실패 · 다음 주기에 재시도", "invalid_response": "비용 응답을 확인할 수 없음", "unavailable": "키 저장소 또는 조회 상태 확인 필요"}.get(snapshot.state, "")
        fields = dict(ai.fields)
        text = f"사용 {fields['USED']} / 총액 {fields['TOTAL']} · {ai.primary_value}"
        if snapshot.period_start and snapshot.period_end:
            text += f"\n집계 시작 {snapshot.period_start} · 다음 초기화 {snapshot.period_end} (UTC)"
        if message: text += "\n" + message
        if snapshot.last_success:
            text += "\n마지막 성공: " + snapshot.last_success.astimezone().strftime("%m-%d %H:%M:%S")
        self.summary.set(text)
