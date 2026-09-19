import os
import sys
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import yt_dlp
import imageio_ffmpeg

APP_NAME = "YouTube MP4 Downloader"

def resource_path(relative_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relative_path)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("760x470")
        self.minsize(700, 430)
        self.configure(bg="#f5f5f7")
        self.msg_queue = queue.Queue()
        self.cancel_requested = False
        self.worker = None

        self.url_var = tk.StringVar()
        self.out_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.quality_var = tk.StringVar(value="最高畫質")
        self.status_var = tk.StringVar(value="準備完成")
        self.progress_var = tk.DoubleVar(value=0)

        self._build_ui()
        self.after(100, self._poll_queue)

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("TButton", font=("Segoe UI", 10), padding=8)
        style.configure("TLabel", background="#f5f5f7", font=("Segoe UI", 10))
        style.configure("Header.TLabel", background="#f5f5f7", font=("Segoe UI Semibold", 20))
        style.configure("Sub.TLabel", background="#f5f5f7", foreground="#666666", font=("Segoe UI", 10))
        style.configure("TEntry", padding=7)
        style.configure("TCombobox", padding=6)

        main = ttk.Frame(self, padding=28)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="YouTube MP4 Downloader", style="Header.TLabel").pack(anchor="w")
        ttk.Label(main, text="貼上 YouTube 連結，直接下載成 MP4。", style="Sub.TLabel").pack(anchor="w", pady=(4, 22))

        ttk.Label(main, text="YouTube 網址").pack(anchor="w")
        url_row = ttk.Frame(main)
        url_row.pack(fill="x", pady=(6, 16))
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var, font=("Segoe UI", 11))
        self.url_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(url_row, text="貼上", command=self._paste).pack(side="left", padx=(8, 0))

        options = ttk.Frame(main)
        options.pack(fill="x", pady=(0, 16))
        left = ttk.Frame(options)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="畫質").pack(anchor="w")
        self.quality = ttk.Combobox(
            left,
            textvariable=self.quality_var,
            values=["最高畫質", "4K", "1440p", "1080p", "720p", "480p"],
            state="readonly",
            width=18,
        )
        self.quality.pack(anchor="w", pady=(6, 0))

        ttk.Label(main, text="儲存位置").pack(anchor="w")
        out_row = ttk.Frame(main)
        out_row.pack(fill="x", pady=(6, 18))
        ttk.Entry(out_row, textvariable=self.out_var, font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="選擇資料夾", command=self._choose_folder).pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(main, variable=self.progress_var, maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))
        ttk.Label(main, textvariable=self.status_var, style="Sub.TLabel").pack(anchor="w")

        btn_row = ttk.Frame(main)
        btn_row.pack(fill="x", pady=(22, 0))
        self.download_btn = ttk.Button(btn_row, text="下載 MP4", command=self._start_download)
        self.download_btn.pack(side="left")
        self.open_btn = ttk.Button(btn_row, text="開啟下載資料夾", command=self._open_folder)
        self.open_btn.pack(side="left", padx=(8, 0))

        note = "請只下載你有權保存的內容，並遵守 YouTube 使用條款與著作權規範。"
        ttk.Label(main, text=note, style="Sub.TLabel").pack(anchor="w", pady=(22, 0))

    def _paste(self):
        try:
            self.url_var.set(self.clipboard_get().strip())
        except Exception:
            pass

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if folder:
            self.out_var.set(folder)

    def _open_folder(self):
        folder = self.out_var.get().strip()
        if not folder:
            return
        os.makedirs(folder, exist_ok=True)
        try:
            os.startfile(folder)
        except Exception as e:
            messagebox.showerror("錯誤", str(e))

    def _format_selector(self):
        limits = {
            "4K": 2160,
            "1440p": 1440,
            "1080p": 1080,
            "720p": 720,
            "480p": 480,
        }
        q = self.quality_var.get()
        if q == "最高畫質":
            return "bv*+ba/b"
        h = limits[q]
        return f"bv*[height<={h}]+ba/b[height<={h}]"

    def _hook(self, d):
        if self.cancel_requested:
            raise yt_dlp.utils.DownloadError("使用者取消下載")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0
            speed = d.get("speed")
            eta = d.get("eta")
            speed_txt = f"{speed/1024/1024:.1f} MB/s" if speed else ""
            eta_txt = f"剩餘 {eta}s" if eta is not None else ""
            self.msg_queue.put(("progress", percent, f"下載中 {percent:.1f}%  {speed_txt}  {eta_txt}".strip()))
        elif status == "finished":
            self.msg_queue.put(("progress", 100, "影片下載完成，正在合併音訊與 MP4…"))

    def _start_download(self):
        url = self.url_var.get().strip()
        out = self.out_var.get().strip()
        if not url:
            messagebox.showwarning("缺少網址", "請先貼上 YouTube 網址。")
            return
        if not out:
            messagebox.showwarning("缺少資料夾", "請選擇儲存位置。")
            return
        os.makedirs(out, exist_ok=True)
        self.download_btn.config(state="disabled")
        self.progress_var.set(0)
        self.status_var.set("正在解析影片…")
        self.cancel_requested = False
        self.worker = threading.Thread(target=self._download, args=(url, out), daemon=True)
        self.worker.start()

    def _download(self, url, out):
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            ffmpeg_dir = os.path.dirname(ffmpeg_exe)

            ydl_opts = {
                "format": self._format_selector(),
                "format_sort": ["res", "vcodec:h264", "acodec:aac"],
                "merge_output_format": "mp4",
                "outtmpl": os.path.join(out, "%(title)s [%(id)s].%(ext)s"),
                "windowsfilenames": True,
                "noplaylist": True,
                "progress_hooks": [self._hook],
                "ffmpeg_location": ffmpeg_dir,
                "retries": 10,
                "fragment_retries": 10,
                "continuedl": True,
                "concurrent_fragment_downloads": 4,
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "影片")
            self.msg_queue.put(("done", title))
        except Exception as e:
            self.msg_queue.put(("error", str(e)))

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.progress_var.set(msg[1])
                    self.status_var.set(msg[2])
                elif kind == "done":
                    self.progress_var.set(100)
                    self.status_var.set(f"完成：{msg[1]}")
                    self.download_btn.config(state="normal")
                    messagebox.showinfo("下載完成", f"{msg[1]}\n\n已儲存到：\n{self.out_var.get()}")
                elif kind == "error":
                    self.download_btn.config(state="normal")
                    self.status_var.set("下載失敗")
                    messagebox.showerror("下載失敗", msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

if __name__ == "__main__":
    app = App()
    app.mainloop()
