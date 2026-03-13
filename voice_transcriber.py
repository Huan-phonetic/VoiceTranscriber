#!/usr/bin/env python3
"""VoiceDecoder — 语音备忘录转写工具（思源笔记集成）"""

import os
import re
import json
import shutil
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from pathlib import Path
from datetime import datetime

import requests

# ── ffmpeg: 若存在则加入 PATH ──────────────────────────────────────────────────
for _candidate in [
    r"C:\ProgramData\chocolatey\bin",
    r"C:\ffmpeg\bin",
    str(Path.home() / "Downloads" / "ffmpeg-master-latest-win64-gpl-shared" / "bin"),
]:
    if os.path.isdir(_candidate) and _candidate not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _candidate + os.pathsep + os.environ.get("PATH", "")

# ── 常量 ──────────────────────────────────────────────────────────────────────
AUDIO_EXTS     = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac", ".wma", ".mp4"}
WHISPER_MODELS = ["turbo", "large-v3", "medium", "small", "base", "tiny"]

CONFIG_FILE    = Path(__file__).parent / "config.json"
PROCESSED_FILE = Path(__file__).parent / "processed.json"

# ── 持久化 ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

def load_processed() -> set:
    if PROCESSED_FILE.exists():
        try:
            return set(json.loads(PROCESSED_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()

def save_processed(processed: set):
    PROCESSED_FILE.write_text(
        json.dumps(sorted(processed), indent=2), encoding="utf-8")

# ── 日期提取 ──────────────────────────────────────────────────────────────────
_RE_PRIMARY = re.compile(r"_(\d{4})(\d{2})(\d{2})[ _-](\d{2})(\d{2})(\d{2})")
_RE_FALLBACKS = [
    re.compile(r"(\d{4})[_\-](\d{2})[_\-](\d{2})[T _\-](\d{2})[:\.](\d{2})[:\.](\d{2})"),
    re.compile(r"(\d{4})[_\-](\d{2})[_\-](\d{2})[T _\-](\d{2})[:\.](\d{2})"),
    re.compile(r"(\d{4})[_\-](\d{2})[_\-](\d{2})"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),
]

def extract_date(path: Path) -> datetime:
    name = path.stem
    m = _RE_PRIMARY.search(name)
    if m:
        try:
            return datetime(*[int(x) for x in m.groups()])
        except ValueError:
            pass
    for pat in _RE_FALLBACKS:
        m = pat.search(name)
        if m:
            g = [int(x) for x in m.groups()]
            try:
                return datetime(*g[:6] if len(g) >= 6 else g[:5] if len(g) >= 5 else g[:3])
            except ValueError:
                continue
    stat = path.stat()
    return datetime.fromtimestamp(min(stat.st_ctime, stat.st_mtime))

# ── Whisper 转写 ──────────────────────────────────────────────────────────────
def transcribe(path: Path, model_name: str, language: str, device: str,
               on_status, on_progress, on_done, on_error):
    def worker():
        try:
            import torch
            from faster_whisper import WhisperModel
            actual_device = ("cuda" if torch.cuda.is_available() else "cpu") \
                            if device == "auto" else device
            compute_type = "float16" if actual_device == "cuda" else "int8"
            on_status(f"加载模型 '{model_name}'（{actual_device}）…")
            model = WhisperModel(model_name, device=actual_device, compute_type=compute_type)
            on_status(f"转写中（{actual_device}）…")
            lang = language if language != "auto" else None
            segments, info = model.transcribe(
                str(path), language=lang, vad_filter=True,
                initial_prompt="以下是普通话的句子，使用简体中文标点符号。",
            )
            total = info.duration or 1
            full_text = ""
            for seg in segments:
                full_text += seg.text
                pct = min(seg.end / total * 100, 100)
                on_progress(seg.text.strip(), pct, seg.end, total)
            on_done(full_text.strip())
        except Exception as e:
            on_error(str(e))
    threading.Thread(target=worker, daemon=True).start()

# ── SiYuan 辅助 ───────────────────────────────────────────────────────────────
def _sy_headers(token: str) -> dict:
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}

def sy_sql(url: str, token: str, stmt: str) -> list[dict]:
    try:
        r = requests.post(f"{url}/api/query/sql",
                          headers=_sy_headers(token), json={"stmt": stmt}, timeout=10)
        return r.json().get("data") or []
    except Exception as e:
        print(f"[SiYuan SQL] {e}")
        return []

def sy_find_doc(url: str, token: str, hpath: str) -> str | None:
    rows = sy_sql(url, token,
                  f"SELECT id FROM blocks WHERE type='d' AND hpath='{hpath}' LIMIT 1")
    return rows[0]["id"] if rows else None

def sy_create_doc(url: str, token: str, notebook: str, path: str, markdown: str) -> str | None:
    try:
        r = requests.post(f"{url}/api/filetree/createDocWithMd",
                          headers=_sy_headers(token),
                          json={"notebook": notebook, "path": path, "markdown": markdown},
                          timeout=15)
        d = r.json()
        return d.get("data") if d.get("code") == 0 else None
    except Exception as e:
        print(f"[SiYuan createDoc] {e}")
        return None

def sy_append(url: str, token: str, parent_id: str, markdown: str) -> bool:
    try:
        r = requests.post(f"{url}/api/block/appendBlock",
                          headers=_sy_headers(token),
                          json={"data": markdown, "dataType": "markdown",
                                "parentID": parent_id},
                          timeout=10)
        return r.json().get("code") == 0
    except Exception as e:
        print(f"[SiYuan append] {e}")
        return False

def push_to_siyuan(cfg: dict, dt: datetime, audio_filename: str, text: str) -> str:
    url      = cfg.get("siyuan_url", "")
    token    = cfg.get("siyuan_token", "")
    notebook = cfg.get("diary_notebook", "")
    root     = cfg.get("diary_root_path", "/daily note")
    if not url or not token or not notebook:
        return "思源未配置，已跳过"
    year, month, day = dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%Y-%m-%d")
    hpath    = f"{root}/{year}/{month}/{day}"
    block_md = f"![{audio_filename}](assets/{audio_filename})\n\n{text}"
    doc_id = sy_find_doc(url, token, hpath)
    if doc_id:
        already = sy_sql(url, token,
                         f"SELECT id FROM blocks WHERE root_id='{doc_id}' "
                         f"AND markdown LIKE '%{audio_filename}%' LIMIT 1")
        if already:
            return f"已存在于 {hpath}"
        ok = sy_append(url, token, doc_id, block_md)
        return ("已追加到" if ok else "追加失败：") + hpath
    else:
        new_id = sy_create_doc(url, token, notebook, hpath, block_md)
        return ("已创建" if new_id else "创建失败：") + hpath

# ── 设置对话框 ────────────────────────────────────────────────────────────────
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.title("设置")
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH)

        def folder_row(label, key, default=""):
            ttk.Label(frame, text=label).pack(anchor=tk.W)
            f = ttk.Frame(frame)
            f.pack(fill=tk.X, pady=(2, 10))
            var = tk.StringVar(value=cfg.get(key, default))
            ttk.Entry(f, textvariable=var, width=56).pack(side=tk.LEFT, fill=tk.X, expand=True)
            def browse(v=var, t=label):
                d = filedialog.askdirectory(title=t)
                if d:
                    v.set(d)
            ttk.Button(f, text="浏览…", command=browse, width=6).pack(side=tk.LEFT, padx=(4, 0))
            return var

        def text_row(label, key, default="", secret=False):
            ttk.Label(frame, text=label).pack(anchor=tk.W)
            var = tk.StringVar(value=cfg.get(key, default))
            ttk.Entry(frame, textvariable=var, show="*" if secret else "",
                      width=62).pack(fill=tk.X, pady=(2, 10))
            return var

        self._rec    = folder_row("录音文件目录（recordings_dir）", "recordings_dir")
        self._assets = folder_row("思源资产目录（assets_dir）",     "assets_dir")
        self._sy_url = text_row("思源 API 地址（siyuan_url）",    "siyuan_url",
                                default="http://127.0.0.1:6806")
        self._token  = text_row("思源 Token（siyuan_token）",     "siyuan_token", secret=True)
        self._nb     = text_row("日记本 Notebook ID",              "diary_notebook")
        self._root   = text_row("日记根路径（diary_root_path）",    "diary_root_path",
                                default="/daily note")

        bot = ttk.Frame(self, padding=(16, 0, 16, 16))
        bot.pack(fill=tk.X)
        ttk.Button(bot, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text="保存", command=self._save).pack(side=tk.RIGHT)

        self.bind("<Return>", lambda _: self._save())
        self.bind("<Escape>", lambda _: self.destroy())
        self.transient(parent)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _save(self):
        self.result = {
            "recordings_dir":  self._rec.get().strip(),
            "assets_dir":      self._assets.get().strip(),
            "siyuan_url":      self._sy_url.get().strip().rstrip("/"),
            "siyuan_token":    self._token.get().strip(),
            "diary_notebook":  self._nb.get().strip(),
            "diary_root_path": self._root.get().strip() or "/daily note",
        }
        self.destroy()

# ── GUI ───────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VoiceDecoder 语音转写")
        self.geometry("1400x860")
        self.minsize(900, 600)

        self.cfg       = load_config()
        self.processed = load_processed()
        self.files: list[Path] = []
        self.idx   = 0
        self._auto = False
        self._busy = False

        self._build_ui()
        self.after(200, self._startup)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        top = ttk.Frame(self, padding=(8, 4))
        top.pack(fill=tk.X)

        ttk.Label(top, text="Whisper 模型:").pack(side=tk.LEFT)
        self._model = tk.StringVar(value=self.cfg.get("model", "medium"))
        ttk.Combobox(top, textvariable=self._model,
                     values=WHISPER_MODELS, width=12).pack(side=tk.LEFT, padx=(2, 10))

        ttk.Label(top, text="语言:").pack(side=tk.LEFT)
        self._lang = tk.StringVar(value=self.cfg.get("language", "zh"))
        ttk.Combobox(top, textvariable=self._lang,
                     values=["zh", "auto", "en", "ja", "ko"], width=6).pack(side=tk.LEFT, padx=(2, 10))

        ttk.Label(top, text="设备:").pack(side=tk.LEFT)
        self._device = tk.StringVar(value=self.cfg.get("device", "auto"))
        ttk.Combobox(top, textvariable=self._device,
                     values=["auto", "cuda", "cpu"], width=6).pack(side=tk.LEFT, padx=(2, 10))

        ttk.Button(top, text="⚙ 设置", command=self._open_settings).pack(side=tk.LEFT, padx=8)

        self._progress_var = tk.StringVar(value="—")
        ttk.Label(top, textvariable=self._progress_var,
                  foreground="#555").pack(side=tk.RIGHT, padx=8)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        lf = ttk.LabelFrame(paned, text="录音文件")
        paned.add(lf, weight=1)
        self._listbox = tk.Listbox(lf, font=("Consolas", 9), selectmode=tk.SINGLE,
                                   activestyle="none")
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(fill=tk.BOTH, expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_list_select)

        rf = ttk.Frame(paned)
        paned.add(rf, weight=2)

        info_f = ttk.LabelFrame(rf, text="文件信息", padding=5)
        info_f.pack(fill=tk.X, padx=4, pady=(4, 2))
        self._info_var = tk.StringVar()
        ttk.Label(info_f, textvariable=self._info_var, font=("Consolas", 9),
                  foreground="#333", wraplength=750, justify=tk.LEFT).pack(anchor=tk.W)

        tf = ttk.LabelFrame(rf, text="转写文字（可编辑）", padding=4)
        tf.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self._text = scrolledtext.ScrolledText(
            tf, font=("Microsoft YaHei", 12), wrap=tk.WORD, undo=True)
        self._text.pack(fill=tk.BOTH, expand=True)

        self._pbar = ttk.Progressbar(rf, mode="determinate", maximum=100)
        self._pbar.pack(fill=tk.X, padx=4, pady=(2, 0))

        self._status_var = tk.StringVar()
        ttk.Label(rf, textvariable=self._status_var, foreground="#0066cc",
                  wraplength=750, justify=tk.LEFT).pack(anchor=tk.W, padx=4, pady=2)

        bot = ttk.Frame(self, padding=(8, 4))
        bot.pack(fill=tk.X)

        self._btn_prev  = ttk.Button(bot, text="← 上一个",  command=self._prev)
        self._btn_prev.pack(side=tk.LEFT, padx=4)
        self._btn_trans = ttk.Button(bot, text="开始转写",   command=self._start_transcription)
        self._btn_trans.pack(side=tk.LEFT, padx=4)
        self._btn_skip  = ttk.Button(bot, text="跳过",       command=self._skip)
        self._btn_skip.pack(side=tk.LEFT, padx=4)
        self._btn_auto  = ttk.Button(bot, text="▶ 自动运行", command=self._toggle_auto,
                                     style="Auto.TButton")
        self._btn_auto.pack(side=tk.LEFT, padx=12)

        self._btn_approve = ttk.Button(bot, text="✓ 批准 → 思源",
                                       command=self._approve, style="Approve.TButton")
        self._btn_approve.pack(side=tk.RIGHT, padx=4)
        self._btn_txt = ttk.Button(bot, text="✓ 仅保存 txt",
                                   command=self._approve_txt_only)
        self._btn_txt.pack(side=tk.RIGHT, padx=4)

        s = ttk.Style()
        s.configure("Approve.TButton", foreground="darkgreen", font=("", 10, "bold"))
        s.configure("Auto.TButton",    foreground="navy",      font=("", 10, "bold"))
        s.configure("AutoOn.TButton",  foreground="white",     font=("", 10, "bold"))

        self.bind("<Control-Return>", lambda _: self._approve())
        self.bind("<Control-Right>",  lambda _: self._skip())
        self.bind("<Control-Left>",   lambda _: self._prev())

    # ── 设置 ──────────────────────────────────────────────────────────────────
    def _open_settings(self):
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.result:
            self.cfg.update(dlg.result)
            save_config(self.cfg)
            self._status("设置已保存。重新扫描录音目录…")
            self.after(300, self._rescan)

    def _check_config(self) -> bool:
        if not self.cfg.get("recordings_dir"):
            messagebox.showinfo("首次使用", "请先配置录音目录和思源笔记连接信息。")
            self._open_settings()
        return bool(self.cfg.get("recordings_dir"))

    # ── 启动 / 扫描 ───────────────────────────────────────────────────────────
    def _startup(self):
        if not self._check_config():
            return
        rec_dir = self.cfg["recordings_dir"]
        files = self._scan(rec_dir)
        total = len(files)
        if total == 0:
            messagebox.showinfo("完成", f"录音目录中没有待处理的文件。\n\n{rec_dir}")
            return
        if not messagebox.askokcancel(
            "VoiceDecoder",
            f"找到 {total} 个待转写录音。\n\n目录：{rec_dir}\n\n点击确定开始。"
        ):
            self.destroy()
            return
        self.files = files
        self._populate_list()
        self._load_current()

    def _rescan(self):
        rec_dir = self.cfg.get("recordings_dir", "")
        if not rec_dir:
            return
        self.files = self._scan(rec_dir)
        self._populate_list()
        if self.files:
            self.idx = 0
            self._load_current()
        else:
            self._status("没有待处理的录音文件。")

    def _scan(self, rec_dir: str) -> list[Path]:
        d = Path(rec_dir)
        if not d.is_dir():
            return []
        return [f for f in sorted(d.iterdir())
                if f.suffix.lower() in AUDIO_EXTS and str(f) not in self.processed]

    def _populate_list(self):
        self._listbox.delete(0, tk.END)
        for f in self.files:
            dt = extract_date(f)
            self._listbox.insert(tk.END, f"  {dt.strftime('%Y-%m-%d')}  {f.stem}")
        if self.files:
            self._listbox.selection_set(0)
        self._update_progress()

    # ── 导航 ──────────────────────────────────────────────────────────────────
    def _on_list_select(self, _event):
        sel = self._listbox.curselection()
        if sel and sel[0] != self.idx:
            self.idx = sel[0]
            self._load_current(auto_start=False)

    def _load_current(self, auto_start=True):
        if self.idx >= len(self.files):
            self._status("所有录音处理完毕！")
            self._set_buttons(True)
            return
        self._listbox.selection_clear(0, tk.END)
        self._listbox.selection_set(self.idx)
        self._listbox.see(self.idx)

        path = self.files[self.idx]
        dt   = extract_date(path)
        size = path.stat().st_size / 1_048_576
        root = self.cfg.get("diary_root_path", "/daily note")
        self._info_var.set(
            f"文件：{path.name}\n"
            f"日期：{dt.strftime('%Y-%m-%d %H:%M:%S')}    "
            f"大小：{size:.1f} MB    "
            f"→ 思源：{root}/{dt.strftime('%Y/%m/%Y-%m-%d')}"
        )
        self._text.delete("1.0", tk.END)
        self._update_progress()
        self._set_buttons(True)

        txt = path.with_suffix(".txt")
        if txt.exists():
            self._text.insert("1.0", txt.read_text(encoding="utf-8"))
            self._status("已有转写文字，检查后批准。")
            if self._auto and auto_start:
                self.after(400, self._approve)
        elif self._auto and auto_start:
            self._start_transcription()
        else:
            self._status("选择文件后点击「开始转写」。")

    def _prev(self):
        if self.idx > 0:
            self.idx -= 1
            self._load_current(auto_start=False)

    def _skip(self):
        self.idx += 1
        self._load_current()

    def _next(self):
        self.idx += 1
        if self.idx < len(self.files):
            self._load_current()
        else:
            self._status("全部完成！")
            if self._auto:
                self._toggle_auto()

    # ── 转写 ──────────────────────────────────────────────────────────────────
    def _start_transcription(self):
        if self._busy or self.idx >= len(self.files):
            return
        self._busy = True
        self._set_buttons(False)
        self._text.delete("1.0", tk.END)
        self._pbar["value"] = 0

        path   = self.files[self.idx]
        model  = self._model.get()
        lang   = self._lang.get()
        device = self._device.get()

        transcribe(
            path, model, lang, device,
            on_status  =lambda msg: self.after(0, lambda m=msg: self._status(m)),
            on_progress=lambda txt, pct, pos, dur: self.after(
                0, lambda t=txt, p=pct, ps=pos, d=dur: self._on_progress(t, p, ps, d)),
            on_done    =lambda txt: self.after(0, lambda t=txt: self._on_done(t)),
            on_error   =lambda err: self.after(0, lambda e=err: self._on_error(e)),
        )

    def _on_progress(self, seg_text: str, pct: float, pos: float, dur: float):
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", seg_text)
        self._pbar["value"] = pct
        e, t = int(pos), int(dur)
        self._status(f"转写中… {e//60}:{e%60:02d} / {t//60}:{t%60:02d}  ({pct:.0f}%)")

    def _on_done(self, text: str):
        self._busy = False
        self._pbar["value"] = 100
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        self._set_buttons(True)
        self.cfg.update({"model": self._model.get(),
                         "language": self._lang.get(),
                         "device": self._device.get()})
        save_config(self.cfg)
        if self._auto:
            self._status("自动模式：转写完成，保存中…")
            self.after(500, self._approve)
        else:
            self._status("转写完成，检查后批准。")

    def _on_error(self, err: str):
        self._busy = False
        self._pbar["value"] = 0
        self._set_buttons(True)
        self._status(f"错误：{err}")
        if self._auto:
            self._toggle_auto()
        messagebox.showerror("转写失败", err)

    # ── 审批 ──────────────────────────────────────────────────────────────────
    def _approve(self):
        text = self._text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("空内容", "没有可保存的转写文字。")
            return
        path = self.files[self.idx]
        dt   = extract_date(path)

        path.with_suffix(".txt").write_text(text, encoding="utf-8")

        assets_dir = self.cfg.get("assets_dir", "")
        copy_msg = "资产目录未配置，已跳过"
        if assets_dir:
            asset_dest = Path(assets_dir) / path.name
            if asset_dest.exists():
                copy_msg = "音频已在资产目录"
            else:
                try:
                    shutil.copy2(str(path), str(asset_dest))
                    copy_msg = "音频已复制"
                except Exception as e:
                    copy_msg = f"复制失败：{e}"

        sy_msg = push_to_siyuan(self.cfg, dt, path.name, text)

        self.processed.add(str(path))
        save_processed(self.processed)
        self._listbox.itemconfig(self.idx, foreground="#888")
        self._status(f"已保存  |  {copy_msg}  |  思源：{sy_msg}")
        self.after(1800, self._next)

    def _approve_txt_only(self):
        text = self._text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("空内容", "没有可保存的转写文字。")
            return
        path = self.files[self.idx]
        path.with_suffix(".txt").write_text(text, encoding="utf-8")
        self.processed.add(str(path))
        save_processed(self.processed)
        self._listbox.itemconfig(self.idx, foreground="#888")
        self._status(f"已保存 {path.stem}.txt（未推送思源）")
        self.after(1000, self._next)

    # ── 自动模式 ──────────────────────────────────────────────────────────────
    def _toggle_auto(self):
        self._auto = not self._auto
        if self._auto:
            self._btn_auto.config(text="■ 停止自动", style="AutoOn.TButton")
            self._status("自动模式已开启，将依次转写并保存所有录音。")
            if not self._busy and not self._text.get("1.0", tk.END).strip():
                self._start_transcription()
        else:
            self._btn_auto.config(text="▶ 自动运行", style="Auto.TButton")
            self._status("自动模式已停止。")

    # ── 辅助 ──────────────────────────────────────────────────────────────────
    def _status(self, msg: str):
        self._status_var.set(msg)

    def _update_progress(self):
        total = len(self.files)
        cur   = self.idx + 1 if self.idx < total else total
        self._progress_var.set(f"{cur} / {total}  待处理")

    def _set_buttons(self, enabled: bool):
        state = tk.NORMAL if enabled else tk.DISABLED
        for btn in (self._btn_trans, self._btn_skip, self._btn_approve,
                    self._btn_txt, self._btn_prev, self._btn_auto):
            btn.config(state=state)


# ── 入口 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    App().mainloop()
