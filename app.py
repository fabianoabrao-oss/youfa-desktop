from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
import uuid
import webbrowser
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

import yt_dlp

APP_NAME = "YouFA"
APP_VERSION = "2.2.4"
AUDD_SIGNUP_URL = "https://dashboard.audd.io/"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".opus"}
TOKEN_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YouTubeAudioOrganizer" / "audd_token.bin"


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def valid_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and (
            host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"))
    except ValueError:
        return False


def download_smoke_test(url: str) -> bool:
    """Valida rede, certificados e extração sem baixar o arquivo de mídia."""
    if not valid_youtube_url(url):
        return False
    node = resource_path("node.exe")
    opts = {
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {"path": str(node)}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return bool(info and info.get("id"))
    except Exception:
        return False


def safe_part(value: str, limit: int = 100) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return (value[:limit] or "desconhecido").strip()


def split_artist_title(value: str) -> tuple[str, str] | None:
    text = re.sub(r"^\s*\d{1,3}\s*[.)\-:]\s*", "", value or "").strip()
    text = re.sub(r"\s*\[[\w-]{6,}\]\s*$", "", text).strip()
    for separator in (" - ", " – ", " — ", " | "):
        if separator in text:
            artist, title = text.split(separator, 1)
            if artist.strip() and title.strip() and len(artist) <= 100:
                return artist.strip(), title.strip()
    return None


def _protect(value: str) -> bytes:
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), "YouTube Audio Organizer - AudD", None, None, None, 0, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _unprotect(data: bytes) -> str:
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def save_token(token: str):
    token = token.strip()
    if len(token) < 20:
        raise ValueError("O token informado parece incompleto.")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(_protect(token))


def load_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return _unprotect(TOKEN_FILE.read_bytes())
    except Exception:
        return None


def delete_token():
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def audd_request(token: str, audio: Path | None = None, url: str | None = None) -> dict:
    boundary = "----AudioOrganizer" + uuid.uuid4().hex
    chunks: list[bytes] = []

    def field(name: str, value: str):
        chunks.extend([f"--{boundary}\r\n".encode(),
                       f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                       value.encode("utf-8"), b"\r\n"])

    field("api_token", token)
    if url:
        field("url", url)
    if audio:
        chunks.extend([f"--{boundary}\r\n".encode(),
                       f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'.encode(),
                       b"Content-Type: audio/mpeg\r\n\r\n", audio.read_bytes(), b"\r\n"])
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        "https://api.audd.io/", data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": f"{APP_NAME}/{APP_VERSION}"}, method="POST")
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        error = payload.get("error") or {}
        raise RuntimeError(error.get("error_message") or "O AudD recusou a solicitação.")
    return payload.get("result") or {}


class QueueLogger:
    def __init__(self, emit): self.emit = emit; self.errors = set()
    def debug(self, message):
        if not str(message).startswith("[debug]"): self.emit("log", str(message))
    def info(self, message): self.emit("log", str(message))
    def warning(self, message): self.emit("log", f"Aviso: {message}")
    def error(self, message):
        self.errors.add(str(message))
        self.emit("log", f"Erro: {message}")


class AudioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION} — Seu organizador de faixas de áudio")
        try: self.iconbitmap(default=str(resource_path("youfa.ico")))
        except tk.TclError: pass
        self.geometry("880x720")
        self.minsize(760, 650)
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.url_var = tk.StringVar()
        self.folder_var = tk.StringVar(value=str(Path.home() / "Music"))
        self.format_var = tk.StringVar(value="mp3")
        self.quality_var = tk.StringVar(value="Melhor")
        self.split_var = tk.StringVar(value="Arquivo inteiro")
        self.playlist_var = tk.BooleanVar(value=True)
        self.identify_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Pronto")
        self.token_status_var = tk.StringVar()
        self.progress_var = tk.DoubleVar(value=0)
        self._build_ui()
        self._refresh_token_status()
        self.after(120, self._drain_events)

    def _build_ui(self):
        self.columnconfigure(0, weight=1); self.rowconfigure(0, weight=1)
        outer = ttk.Frame(self, padding=18); outer.grid(sticky="nsew")
        outer.columnconfigure(0, weight=1); outer.rowconfigure(3, weight=1)
        form = ttk.LabelFrame(outer, text="Origem e destino", padding=12)
        form.grid(row=0, column=0, sticky="ew"); form.columnconfigure(1, weight=1)
        ttk.Label(form, text="URL do YouTube").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.url_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(form, text="Salvar em").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(form, textvariable=self.folder_var).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Button(form, text="Selecionar...", command=self._choose_folder).grid(row=1, column=2, padx=(8, 0), pady=5)

        options = ttk.LabelFrame(outer, text="Opções", padding=12)
        options.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for col in range(4): options.columnconfigure(col, weight=1)
        ttk.Label(options, text="Formato").grid(row=0, column=0, sticky="w")
        ttk.Combobox(options, textvariable=self.format_var, values=("mp3", "m4a", "flac", "wav", "opus"), state="readonly").grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(options, text="Qualidade").grid(row=0, column=1, sticky="w")
        ttk.Combobox(options, textvariable=self.quality_var, values=("Melhor", "Alta", "Média", "Compacta"), state="readonly").grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Label(options, text="Modo de salvamento").grid(row=0, column=2, sticky="w")
        ttk.Combobox(options, textvariable=self.split_var, values=("Arquivo inteiro", "Separar por faixas"), state="readonly").grid(row=1, column=2, sticky="ew", padx=8)
        ttk.Checkbutton(options, text="Aceitar playlist", variable=self.playlist_var).grid(row=1, column=3, sticky="w", padx=(8, 0))
        ttk.Label(options, text="A separação usa análise acústica local e não consulta serviços externos.", foreground="#555").grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        recognition = ttk.LabelFrame(outer, text="Identificação das músicas", padding=12)
        recognition.grid(row=2, column=0, sticky="ew", pady=12); recognition.columnconfigure(1, weight=1)
        ttk.Checkbutton(recognition, text="Identificar artista e música", variable=self.identify_var).grid(row=0, column=0, sticky="w")
        ttk.Label(recognition, textvariable=self.token_status_var).grid(row=0, column=1, sticky="w", padx=12)
        ttk.Button(recognition, text="Configurar AudD...", command=self._token_dialog).grid(row=0, column=2)
        ttk.Label(recognition, text="Desmarcado por padrão. O AudD só será consultado se você marcar esta opção; oferece 300 testes gratuitos e depois é pago.", foreground="#555", wraplength=780).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        log_frame = ttk.LabelFrame(outer, text="Progresso", padding=10)
        log_frame.grid(row=3, column=0, sticky="nsew"); log_frame.columnconfigure(0, weight=1); log_frame.rowconfigure(1, weight=1)
        ttk.Progressbar(log_frame, variable=self.progress_var, maximum=100).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.log = tk.Text(log_frame, height=15, wrap="word", state="disabled"); self.log.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview); scroll.grid(row=1, column=1, sticky="ns"); self.log.configure(yscrollcommand=scroll.set)
        footer = ttk.Frame(outer); footer.grid(row=4, column=0, sticky="ew", pady=(12, 0)); footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.cancel_button = ttk.Button(footer, text="Cancelar", command=self._cancel, state="disabled"); self.cancel_button.grid(row=0, column=1, padx=8)
        ttk.Button(footer, text="Sobre e créditos", command=self._show_credits).grid(row=0, column=1, padx=8)
        self.cancel_button.grid_configure(column=2)
        self.start_button = ttk.Button(footer, text="Baixar áudio", command=self._start); self.start_button.grid(row=0, column=3)
        ttk.Label(outer, text="YouFA — Seu organizador de faixas de áudio · by Fabiano Abrão", foreground="#555").grid(row=5, column=0, sticky="w", pady=(10, 0))
        ttk.Label(outer, text="Use apenas conteúdo próprio, autorizado ou cujo download seja permitido.", foreground="#666").grid(row=6, column=0, sticky="w", pady=(4, 0))

    def _show_credits(self):
        window = tk.Toplevel(self); window.title("Sobre o YouFA"); window.geometry("700x500")
        frame = ttk.Frame(window, padding=18); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="YouFA", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Seu organizador de faixas de áudio\nby Fabiano Abrão", font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 12))
        credits = tk.Text(frame, wrap="word", relief="solid", borderwidth=1, padx=10, pady=10)
        credits.pack(fill="both", expand=True)
        credits.insert("1.0", resource_path("CREDITS.txt").read_text(encoding="utf-8"))
        credits.configure(state="disabled")
        ttk.Button(frame, text="Fechar", command=window.destroy).pack(anchor="e", pady=(12, 0))
        window.transient(self); window.grab_set()

    def _choose_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if chosen: self.folder_var.set(chosen)

    def _refresh_token_status(self):
        self.token_status_var.set("Token AudD protegido no Windows" if load_token() else "Token AudD não configurado")

    def _token_dialog(self):
        window = tk.Toplevel(self); window.title("Configurar reconhecimento AudD"); window.resizable(False, False)
        frame = ttk.Frame(window, padding=18); frame.grid()
        ttk.Label(frame, text="Token da API AudD").grid(row=0, column=0, sticky="w")
        token_var = tk.StringVar(); entry = ttk.Entry(frame, textvariable=token_var, show="•", width=52)
        entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 10))
        ttk.Label(frame, text="O AudD oferece 300 identificações gratuitas. Depois, o serviço é pago conforme o uso. O token será criptografado para este usuário do Windows.", wraplength=520).grid(row=2, column=0, columnspan=3, sticky="w")
        def save():
            try:
                save_token(token_var.get()); self._refresh_token_status()
                messagebox.showinfo(APP_NAME, "Token salvo de forma protegida.", parent=window); window.destroy()
            except Exception as exc: messagebox.showerror(APP_NAME, str(exc), parent=window)
        def test():
            token = token_var.get().strip()
            if not token:
                messagebox.showerror(APP_NAME, "Informe o token.", parent=window); return
            if not messagebox.askyesno(APP_NAME, "O teste consumirá 1 identificação gratuita. Continuar?", parent=window): return
            try:
                result = audd_request(token, url="https://audd.tech/example.mp3")
                messagebox.showinfo(APP_NAME, f"Token válido.\n\n{result.get('artist', '')} — {result.get('title', '')}", parent=window)
            except Exception as exc: messagebox.showerror(APP_NAME, f"O teste falhou:\n{exc}", parent=window)
        ttk.Button(frame, text="Criar conta / contratar", command=lambda: webbrowser.open(AUDD_SIGNUP_URL)).grid(row=3, column=0, sticky="w", pady=(14, 0))
        ttk.Button(frame, text="Testar (1 consulta)", command=test).grid(row=3, column=1, padx=8, pady=(14, 0))
        ttk.Button(frame, text="Salvar token", command=save).grid(row=3, column=2, pady=(14, 0))
        if TOKEN_FILE.exists():
            ttk.Button(frame, text="Apagar token salvo", command=lambda: (delete_token(), self._refresh_token_status(), window.destroy())).grid(row=4, column=0, sticky="w", pady=(12, 0))
        entry.focus_set(); window.transient(self); window.grab_set()

    def _emit(self, kind: str, value): self.events.put((kind, value))
    def _write_log(self, message: str):
        self.log.configure(state="normal"); self.log.insert("end", message.rstrip() + "\n"); self.log.see("end"); self.log.configure(state="disabled")
    def _drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log": self._write_log(value)
                elif kind == "progress": self.progress_var.set(value)
                elif kind == "status": self.status_var.set(value)
                elif kind == "review": self._review_results(value)
                elif kind == "split_failed": self._show_split_failed(Path(value))
                elif kind == "done": self._finish(bool(value))
        except queue.Empty: pass
        self.after(120, self._drain_events)

    def _show_split_failed(self, folder: Path):
        window = tk.Toplevel(self); window.title("Separação não realizada"); window.resizable(False, False)
        frame = ttk.Frame(window, padding=18); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Não foram encontrados cortes confiáveis.\nO arquivo inteiro foi preservado na pasta de destino.", justify="left").pack(anchor="w")
        link = tk.Label(frame, text=str(folder), fg="#0563C1", cursor="hand2", wraplength=620, justify="left")
        link.pack(anchor="w", pady=(12, 14)); link.bind("<Button-1>", lambda _event: os.startfile(str(folder)))
        ttk.Button(frame, text="Abrir pasta", command=lambda: os.startfile(str(folder))).pack(side="left")
        ttk.Button(frame, text="Fechar", command=window.destroy).pack(side="right")
        window.transient(self); window.grab_set()

    def _review_results(self, request: dict):
        window = tk.Toplevel(self); window.title("Revisar identificação"); window.geometry("900x430")
        frame = ttk.Frame(window, padding=12); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Confira os nomes. Aplicar irá renomear os arquivos e gravar as tags.").pack(anchor="w", pady=(0, 8))
        tree = ttk.Treeview(frame, columns=("file", "artist", "title", "source"), show="headings")
        for key, label, width in (("file", "Arquivo atual", 300), ("artist", "Artista", 180), ("title", "Música", 240), ("source", "Origem", 100)):
            tree.heading(key, text=label); tree.column(key, width=width, anchor="w")
        for row in request["rows"]: tree.insert("", "end", values=(Path(row["path"]).name, row["artist"], row["title"], row["source"]))
        tree.pack(fill="both", expand=True); buttons = ttk.Frame(frame); buttons.pack(fill="x", pady=(10, 0))
        def close(approved: bool):
            request["approved"] = approved; window.destroy(); request["event"].set()
        ttk.Button(buttons, text="Não renomear", command=lambda: close(False)).pack(side="right")
        ttk.Button(buttons, text="Aplicar nomes e tags", command=lambda: close(True)).pack(side="right", padx=8)
        window.protocol("WM_DELETE_WINDOW", lambda: close(False)); window.transient(self); window.grab_set()

    def _start(self):
        url, folder = self.url_var.get().strip(), Path(self.folder_var.get().strip())
        if not valid_youtube_url(url): messagebox.showerror(APP_NAME, "Informe uma URL válida do YouTube."); return
        try:
            folder.mkdir(parents=True, exist_ok=True); probe = folder / ".youtube_audio_write_test"; probe.write_text("ok", encoding="utf-8"); probe.unlink()
        except OSError as exc: messagebox.showerror(APP_NAME, f"A pasta selecionada não pode ser usada:\n{exc}"); return
        self.cancel_event.clear(); self.progress_var.set(0); self.start_button.configure(state="disabled"); self.cancel_button.configure(state="normal"); self._write_log("—" * 56)
        args = (url, folder, self.format_var.get(), self.quality_var.get(), self.split_var.get(), self.playlist_var.get(), self.identify_var.get())
        threading.Thread(target=self._download, args=args, daemon=True).start()
    def _cancel(self): self.cancel_event.set(); self.status_var.set("Cancelando..."); self.cancel_button.configure(state="disabled")
    def _finish(self, success: bool):
        self.start_button.configure(state="normal"); self.cancel_button.configure(state="disabled")
        warnings = success and getattr(self, "_download_warnings", False)
        self.status_var.set(("Concluído com avisos" if warnings else "Concluído") if success else ("Cancelado" if self.cancel_event.is_set() else "Falha — consulte o log"))
        if success:
            self.progress_var.set(100)
            if warnings: messagebox.showwarning(APP_NAME, "Processamento concluído com falhas em alguns itens. Os arquivos disponíveis foram processados. Consulte o log.")
            else: messagebox.showinfo(APP_NAME, "Processamento concluído.")
    def _progress_hook(self, data):
        if self.cancel_event.is_set(): raise yt_dlp.utils.DownloadCancelled("Operação cancelada pelo usuário")
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            if total: self._emit("progress", min(85, data.get("downloaded_bytes", 0) * 85 / total))
            self._emit("status", f"Baixando {data.get('_percent_str', '').strip()}")
        elif data.get("status") == "finished": self._emit("progress", 88); self._emit("status", "Convertendo o áudio...")

    def _download(self, url, folder, audio_format, quality, split_mode, allow_playlist, identify):
        self._download_warnings = False
        self._track_metadata = {}
        self._split_outputs = set()
        success = False; before = {p.resolve() for p in folder.rglob("*") if p.is_file()}
        ffmpeg, node = resource_path("ffmpeg.exe"), resource_path("node.exe")
        quality_map = {"Melhor": "0", "Alta": "2", "Média": "5", "Compacta": "7"}
        opts = {"format": "bestaudio/best", "outtmpl": {"default": str(folder / "%(title).180B [%(id)s].%(ext)s"), "chapter": str(folder / "%(section_number)02d - %(section_title).150B [%(id)s].%(ext)s")}, "windowsfilenames": True, "noplaylist": not allow_playlist, "continuedl": True, "overwrites": False, "progress_hooks": [self._progress_hook], "logger": QueueLogger(self._emit), "ffmpeg_location": str(ffmpeg.parent), "js_runtimes": {"node": {"path": str(node)}}, "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": audio_format, "preferredquality": quality_map[quality]}, {"key": "FFmpegMetadata", "add_metadata": True}], "quiet": True}
        opts.update(ignoreerrors=bool(allow_playlist), match_filter=self._download_filter)
        try:
            if not ffmpeg.exists() or not node.exists(): raise RuntimeError("Componentes internos não encontrados.")
            self._emit("log", f"Destino: {folder}")
            with yt_dlp.YoutubeDL(opts) as ydl: info = ydl.extract_info(url, download=True)
            if self.cancel_event.is_set(): raise RuntimeError("Operação cancelada")
            self._download_warnings = bool(opts["logger"].errors)
            if self._download_warnings:
                self._emit("log", "Alguns itens falharam ou estão indisponíveis. Continuando com os arquivos disponíveis; detalhes acima.")
            files = self._new_audio_files(folder, before)
            # A preserved full file can be reused on a later attempt with the same URL.
            source_ids = {str(entry.get("id")) for entry in self._flatten_info(info) if entry.get("id")}
            for path in folder.iterdir():
                if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS and any(path.stem.endswith(f"[{video_id}]") for video_id in source_ids):
                    if path.resolve() not in files: files.append(path.resolve())
            if not files:
                raise RuntimeError("Nenhum arquivo de áudio disponível para processar. Confira os erros dos itens acima.")
            if split_mode == "Separar por faixas":
                separated = []
                for audio in files:
                    entry = self._entry_for_file(audio, info)
                    if entry and entry.get("chapters"):
                        tracks = self._split_chapters(audio, ffmpeg, entry)
                    else:
                        self._emit("log", "Sem capítulos: separação experimental por pausas, sem cortes forçados; o arquivo inteiro será preservado.")
                        tracks = self._split_acoustically(audio, ffmpeg)
                    if tracks:
                        self._split_outputs.update(tracks)
                        separated.extend(tracks)
                    else:
                        separated.append(audio)
                        self._emit("split_failed", str(audio.parent))
                files = separated
            if identify:
                rows = self._identify_files(files, info, ffmpeg)
                if rows:
                    request = {"rows": rows, "approved": False, "event": threading.Event()}; self._emit("review", request); request["event"].wait()
                    if request["approved"]: self._apply_names(rows, ffmpeg)
            self._emit("log", "Concluído com avisos: os itens com falha não foram concluídos; confira o log." if self._download_warnings else "Concluído com sucesso."); success = True
        except Exception as exc: self._emit("log", "Operação cancelada." if self.cancel_event.is_set() else f"Falha: {exc}")
        finally: self._emit("done", success)

    @staticmethod
    def _new_audio_files(folder, before):
        return sorted([p.resolve() for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS and p.resolve() not in before], key=lambda p: str(p).lower())
    def _flatten_info(self, info):
        if not isinstance(info, dict): return []
        if "entries" in info:
            result = []
            for entry in info["entries"] or []:
                if entry: result.extend(self._flatten_info(entry))
            return result
        return [info]

    def _download_filter(self, info, *, incomplete=False):
        if self.cancel_event.is_set():
            raise yt_dlp.utils.DownloadCancelled("Operação cancelada pelo usuário")
        return None
    def _entry_for_file(self, path, info):
        entries = self._flatten_info(info); match_id = re.search(r"\[([\w-]{6,})\](?=\.[^.]+$)", path.name)
        entry = next((x for x in entries if match_id and x.get("id") == match_id.group(1)), entries[0] if len(entries) == 1 else None)
        return entry

    def _free_metadata(self, path, info):
        if path in getattr(self, "_track_metadata", {}):
            return self._track_metadata[path]
        if path in getattr(self, "_split_outputs", set()):
            return None
        entry = self._entry_for_file(path, info)
        if not entry: return split_artist_title(path.stem)
        artist = entry.get("artist") or (", ".join(entry.get("artists") or []) if entry.get("artists") else None)
        title = entry.get("track") or entry.get("alt_title")
        if artist and title: return str(artist), str(title)
        return split_artist_title(entry.get("title", "")) or split_artist_title(path.stem)

    def _split_chapters(self, audio, ffmpeg, entry):
        """Use source timestamps, never acoustic segment indexes, for chapter labels."""
        created = []
        try:
            duration = self._duration(audio, ffmpeg)
            if not duration: raise ValueError("Duração do áudio indisponível")
            chapters = entry["chapters"]
            spans = []
            cursor = 0.0
            for chapter in chapters:
                start = float(chapter["start_time"])
                end = float(chapter["end_time"])
                if not (abs(start - cursor) <= 1.0 and start < end <= duration + 1.0):
                    raise ValueError("Capítulos sobrepostos, incompletos ou fora da duração")
                spans.append((cursor, min(end, duration), chapter.get("title") or ""))
                cursor = min(end, duration)
            if len(spans) < 2 or abs(cursor - duration) > 1.0:
                raise ValueError("Capítulos não cobrem o áudio completo")
            spans[-1] = (spans[-1][0], duration, spans[-1][2])
            target = audio.parent / f"{safe_part(audio.stem, 140)} - faixas"
            target.mkdir(exist_ok=True)
            # A fresh directory prevents overwriting tracks from an earlier run.
            target = target / uuid.uuid4().hex[:8]
            target.mkdir()
            for index, (start, end, title) in enumerate(spans, 1):
                if self.cancel_event.is_set(): raise RuntimeError("Operação cancelada")
                dest = target / f"{index:02d} - {safe_part(title or 'faixa', 130)}{audio.suffix.lower()}"
                subprocess.run([str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-i", str(audio), "-t", str(end - start), "-map", "0:a:0", "-c", "copy", str(dest)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
                created.append(dest.resolve())
                # Chapter titles may be "song – artist", "artist – song", or medleys.
                # Preserve their text instead of guessing the ordering of the tags.
                self._track_metadata[dest.resolve()] = (str(entry.get("artist") or ""), title) if title else None
                self._emit("log", f"Capítulo {index:02d}: {start:.3f}–{end:.3f}s — {title or 'Sem título'}")
        except Exception as exc:
            for path in created:
                path.unlink(missing_ok=True)
                self._track_metadata.pop(path, None)
            self._emit("log", f"Separação por capítulos falhou: {exc}; arquivo inteiro preservado.")
            return []
        audio.unlink()
        self._emit("log", f"{len(created)} faixas separadas pelos horários dos capítulos.")
        return created
    def _duration(self, path, ffmpeg):
        result = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if not match: return None
        h, m, s = match.groups(); return int(h) * 3600 + int(m) * 60 + float(s)
    def _audd_for_file(self, path, ffmpeg, token):
        duration = self._duration(path, ffmpeg)
        if not duration or duration > 15 * 60: return None
        with tempfile.TemporaryDirectory(prefix="audio-organizer-") as temp:
            sample = Path(temp) / "sample.mp3"; start = max(0, duration / 2 - 6)
            subprocess.run([str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", "12", "-i", str(path), "-vn", "-ac", "1", "-b:a", "64k", str(sample)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            result = audd_request(token, audio=sample)
        return (str(result["artist"]), str(result["title"])) if result.get("artist") and result.get("title") else None
    def _identify_files(self, files, info, ffmpeg):
        token, rows, api_calls = load_token(), [], 0
        for path in files:
            if self.cancel_event.is_set(): break
            metadata, source = self._free_metadata(path, info), "YouTube"
            if not metadata and token:
                self._emit("status", f"Consultando AudD: {path.name}")
                try: metadata = self._audd_for_file(path, ffmpeg, token); api_calls += 1; source = "AudD"
                except Exception as exc: self._emit("log", f"AudD não identificou {path.name}: {exc}")
            if metadata: rows.append({"path": str(path), "artist": metadata[0], "title": metadata[1], "source": source})
            else: self._emit("log", f"Sem identificação: {path.name}")
        self._emit("log", f"Identificações AudD consumidas nesta execução: {api_calls}"); return rows
    def _apply_names(self, rows, ffmpeg):
        for index, row in enumerate(rows, 1):
            source = Path(row["path"])
            if not source.exists(): continue
            artist, title = safe_part(row["artist"]) if row["artist"] else "", safe_part(row["title"], 130)
            number_match = re.match(r"(\d{1,3})", source.name); number = int(number_match.group(1)) if number_match else index
            temp = source.with_name(f".tagging-{uuid.uuid4().hex}{source.suffix}")
            subprocess.run([str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-map", "0:a:0", "-c", "copy", "-metadata", f"artist={row['artist']}", "-metadata", f"title={row['title']}", "-metadata", f"track={number}", str(temp)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            label = f"{artist} - {title}" if artist else title
            os.replace(temp, source); target = source.with_name(f"{number:02d} - {label}{source.suffix.lower()}"); suffix = 2
            while target.exists() and target != source: target = source.with_name(f"{number:02d} - {label} ({suffix}){source.suffix.lower()}"); suffix += 1
            if target != source: source.rename(target)
            self._emit("log", f"Salvo: {target.name}")
    @staticmethod
    def _pause_boundaries(log, duration):
        """Only sustained quiet pauses justify an automatic, experimental cut."""
        pauses = []
        start = None
        for event, value in re.findall(r"silence_(start|end):\s*([0-9.]+)", log):
            if event == "start":
                start = float(value)
            elif start is not None:
                end = float(value)
                if end > start:
                    if pauses and start - pauses[-1][1] <= 0.15:
                        pauses[-1] = (pauses[-1][0], end)
                    else:
                        pauses.append((start, end))
                start = None
        # Prefer the longest pause when several are too close to be separate tracks.
        selected = []
        for start, end in sorted(pauses, key=lambda span: span[1] - span[0], reverse=True):
            point = (start + end) / 2
            if end - start >= 0.8 and 75 <= point <= duration - 75:
                if all(abs(point - other) >= 75 for other in selected):
                    selected.append(point)
        return sorted(selected)

    def _split_acoustically(self, audio, ffmpeg):
        self._emit("status", f"Procurando pausas entre músicas em {audio.name}...")
        created = []
        try:
            duration = self._duration(audio, ffmpeg)
            if not duration: raise ValueError("Duração do áudio indisponível")
            process = subprocess.Popen([
                str(ffmpeg), "-hide_banner", "-nostats", "-i", str(audio),
                "-vn", "-af", "silencedetect=noise=-35dB:d=0.5", "-f", "null", "-",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            while True:
                if self.cancel_event.is_set():
                    process.kill(); process.communicate()
                    raise RuntimeError("Operação cancelada")
                try:
                    _, log = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode: raise RuntimeError("Não foi possível analisar as pausas")
            cuts = self._pause_boundaries(log, duration)
            if not cuts:
                self._emit("log", "Nenhuma pausa confiável para separar; arquivo inteiro preservado. Não foram forçados cortes.")
                return []
            boundaries = [0.0] + cuts + [duration]
            target = audio.parent / f"{safe_part(audio.stem, 140)} - faixas" / uuid.uuid4().hex[:8]
            target.mkdir(parents=True)
            for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
                if self.cancel_event.is_set(): raise RuntimeError("Operação cancelada")
                dest = target / f"{index:02d} - faixa{audio.suffix.lower()}"
                created.append(dest.resolve())
                subprocess.run([str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(start), "-i", str(audio), "-t", str(end - start),
                    "-map", "0:a:0", "-c", "copy", str(dest)], check=True,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                self._emit("log", f"Trecho {index:02d}: {start:.3f}–{end:.3f}s")
        except Exception as exc:
            for path in created: path.unlink(missing_ok=True)
            self._emit("log", f"Separação interrompida: {exc}; arquivo inteiro preservado: {audio}")
            return []
        self._emit("log", f"{len(created)} trechos separados por pausas (experimental). Confira as transições; músicas sem pausa podem permanecer juntas. Arquivo inteiro preservado: {audio}")
        return created


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        raise SystemExit(0 if download_smoke_test(sys.argv[2]) else 1)
    AudioApp().mainloop()
