import ctypes
import os
import queue
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import imageio_ffmpeg
import yt_dlp

APP_NAME = "YouTube MP4 Downloader"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("920x650")
        self.minsize(820, 610)

        self.msg_queue = queue.Queue()
        self.cancel_requested = False
        self.worker = None

        self.url_var = ctk.StringVar()
        self.out_var = ctk.StringVar(value=str(Path.home() / "Downloads"))
        self.quality_var = ctk.StringVar(value="最高畫質")
        self.status_var = ctk.StringVar(value="準備完成")
        self.detail_var = ctk.StringVar(value="貼上 YouTube 連結後即可開始下載")
        self.theme_var = ctk.StringVar(value="系統")

        self._build_ui()
        self.after(100, self._poll_queue)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=34, pady=(28, 12))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="YouTube MP4 Downloader",
            font=ctk.CTkFont(family="Segoe UI", size=27, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="乾淨、直接，把影片下載成 MP4",
            text_color=("gray38", "gray70"),
            font=ctk.CTkFont(family="Segoe UI", size=14),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        theme = ctk.CTkSegmentedButton(
            header,
            values=["淺色", "系統", "深色"],
            variable=self.theme_var,
            command=self._change_theme,
            width=200,
            height=34,
            corner_radius=10,
        )
        theme.grid(row=0, column=1, rowspan=2, sticky="e")

        card = ctk.CTkFrame(self, corner_radius=22, border_width=1, border_color=("gray85", "gray24"))
        card.grid(row=1, column=0, sticky="nsew", padx=34, pady=(0, 28))
        card.grid_columnconfigure(0, weight=1)

        section = ctk.CTkFrame(card, fg_color="transparent")
        section.grid(row=0, column=0, sticky="ew", padx=26, pady=(26, 18))
        section.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            section,
            text="影片連結",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        url_row = ctk.CTkFrame(section, fg_color="transparent")
        url_row.grid(row=1, column=0, sticky="ew")
        url_row.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            url_row,
            textvariable=self.url_var,
            placeholder_text="https://www.youtube.com/watch?v=...",
            height=48,
            corner_radius=14,
            font=ctk.CTkFont(family="Segoe UI", size=14),
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkButton(
            url_row,
            text="貼上",
            width=96,
            height=48,
            corner_radius=14,
            command=self._paste,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
        ).grid(row=0, column=1)

        options = ctk.CTkFrame(card, fg_color="transparent")
        options.grid(row=1, column=0, sticky="ew", padx=26, pady=(0, 18))
        options.grid_columnconfigure((0, 1), weight=1)

        quality_box = ctk.CTkFrame(options, fg_color="transparent")
        quality_box.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ctk.CTkLabel(
            quality_box,
            text="畫質",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        self.quality_menu = ctk.CTkOptionMenu(
            quality_box,
            values=["最高畫質", "4K", "1440p", "1080p", "720p", "480p"],
            variable=self.quality_var,
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(family="Segoe UI", size=14),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self.quality_menu.pack(fill="x")

        format_box = ctk.CTkFrame(options, fg_color="transparent")
        format_box.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        ctk.CTkLabel(
            format_box,
            text="輸出格式",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        self.format_display = ctk.CTkEntry(
            format_box,
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(family="Segoe UI", size=14),
        )
        self.format_display.pack(fill="x")
        self.format_display.insert(0, "MP4")
        self.format_display.configure(state="disabled")

        save = ctk.CTkFrame(card, fg_color="transparent")
        save.grid(row=2, column=0, sticky="ew", padx=26, pady=(0, 18))
        save.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            save,
            text="儲存位置",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        path_row = ctk.CTkFrame(save, fg_color="transparent")
        path_row.grid(row=1, column=0, sticky="ew")
        path_row.grid_columnconfigure(0, weight=1)

        ctk.CTkEntry(
            path_row,
            textvariable=self.out_var,
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(family="Segoe UI", size=13),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkButton(
            path_row,
            text="選擇資料夾",
            width=118,
            height=44,
            corner_radius=12,
            fg_color=("gray78", "gray26"),
            hover_color=("gray70", "gray32"),
            text_color=("gray12", "gray92"),
            command=self._choose_folder,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        ).grid(row=0, column=1)

        status_card = ctk.CTkFrame(card, corner_radius=16, fg_color=("gray96", "gray15"))
        status_card.grid(row=3, column=0, sticky="ew", padx=26, pady=(2, 20))
        status_card.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            status_card,
            textvariable=self.status_var,
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 2))

        self.detail_label = ctk.CTkLabel(
            status_card,
            textvariable=self.detail_var,
            text_color=("gray42", "gray68"),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            anchor="w",
        )
        self.detail_label.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        self.progress = ctk.CTkProgressBar(status_card, height=8, corner_radius=4)
        self.progress.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        self.progress.set(0)

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=26, pady=(0, 24))
        actions.grid_columnconfigure(0, weight=1)

        secondary = ctk.CTkFrame(actions, fg_color="transparent")
        secondary.grid(row=0, column=0, sticky="w")

        self.open_btn = ctk.CTkButton(
            secondary,
            text="開啟資料夾",
            width=112,
            height=44,
            corner_radius=13,
            fg_color="transparent",
            border_width=1,
            border_color=("gray72", "gray35"),
            text_color=("gray16", "gray90"),
            hover_color=("gray90", "gray22"),
            command=self._open_folder,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        self.open_btn.pack(side="left")

        self.cancel_btn = ctk.CTkButton(
            secondary,
            text="取消",
            width=86,
            height=44,
            corner_radius=13,
            fg_color="transparent",
            border_width=1,
            border_color=("gray72", "gray35"),
            text_color=("gray16", "gray90"),
            hover_color=("gray90", "gray22"),
            command=self._cancel,
            state="disabled",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        self.cancel_btn.pack(side="left", padx=(10, 0))

        self.download_btn = ctk.CTkButton(
            actions,
            text="下載 MP4",
            width=150,
            height=46,
            corner_radius=14,
            command=self._start_download,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
        )
        self.download_btn.grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(
            card,
            text="僅下載你有權保存的內容，並遵守 YouTube 使用條款與著作權規範。",
            text_color=("gray50", "gray58"),
            font=ctk.CTkFont(family="Segoe UI", size=11),
        ).grid(row=5, column=0, sticky="w", padx=28, pady=(0, 20))

    def _change_theme(self, value):
        mapping = {"淺色": "Light", "系統": "System", "深色": "Dark"}
        ctk.set_appearance_mode(mapping[value])

    def _paste(self):
        try:
            self.url_var.set(self.clipboard_get().strip())
            self.url_entry.focus_set()
            self.url_entry.icursor("end")
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
        except Exception as exc:
            messagebox.showerror("無法開啟資料夾", str(exc))

    def _cancel(self):
        self.cancel_requested = True
        self.status_var.set("正在取消…")
        self.detail_var.set("會在目前的下載片段結束後停止")

    def _format_selector(self):
        limits = {"4K": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}
        choice = self.quality_var.get()
        if choice == "最高畫質":
            return "bv*+ba/b"
        height = limits[choice]
        return f"bv*[height<={height}]+ba/b[height<={height}]"

    def _hook(self, data):
        if self.cancel_requested:
            raise yt_dlp.utils.DownloadError("使用者取消下載")

        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0
            speed = data.get("speed")
            eta = data.get("eta")

            parts = []
            if speed:
                parts.append(f"{speed / 1024 / 1024:.1f} MB/s")
            if eta is not None:
                parts.append(f"約 {eta} 秒")
            detail = " · ".join(parts) if parts else "正在下載影片資料"
            self.msg_queue.put(("progress", percent, f"下載中 {percent:.1f}%", detail))

        elif status == "finished":
            self.msg_queue.put(("progress", 100, "正在處理影片", "合併影像與音訊並輸出 MP4"))

    def _set_busy(self, busy):
        self.download_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.quality_menu.configure(state="disabled" if busy else "normal")

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
        self.cancel_requested = False
        self.progress.set(0)
        self.status_var.set("正在解析影片")
        self.detail_var.set("正在向 YouTube 取得可用畫質…")
        self._set_busy(True)

        self.worker = threading.Thread(target=self._download, args=(url, out), daemon=True)
        self.worker.start()

    def _download(self, url, out):
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            if not os.path.isfile(ffmpeg_exe):
                raise RuntimeError(f"內建 FFmpeg 找不到：{ffmpeg_exe}")

            options = {
                "format": self._format_selector(),
                "format_sort": ["res", "vcodec:h264", "acodec:aac"],
                "merge_output_format": "mp4",
                "outtmpl": os.path.join(out, "%(title)s [%(id)s].%(ext)s"),
                "windowsfilenames": True,
                "noplaylist": True,
                "progress_hooks": [self._hook],
                "ffmpeg_location": ffmpeg_exe,
                "retries": 10,
                "fragment_retries": 10,
                "continuedl": True,
                "concurrent_fragment_downloads": 4,
                "quiet": True,
                "no_warnings": True,
            }

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "影片")

            self.msg_queue.put(("done", title))

        except Exception as exc:
            self.msg_queue.put(("error", str(exc)))

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]

                if kind == "progress":
                    percent = msg[1]
                    self.progress.set(max(0, min(1, percent / 100)))
                    self.status_var.set(msg[2])
                    self.detail_var.set(msg[3])

                elif kind == "done":
                    self.progress.set(1)
                    self.status_var.set("下載完成")
                    self.detail_var.set(msg[1])
                    self._set_busy(False)
                    messagebox.showinfo("下載完成", f"{msg[1]}\n\n已儲存到：\n{self.out_var.get()}")

                elif kind == "error":
                    self._set_busy(False)
                    if self.cancel_requested:
                        self.progress.set(0)
                        self.status_var.set("已取消")
                        self.detail_var.set("下載已停止")
                    else:
                        self.status_var.set("下載失敗")
                        self.detail_var.set("請查看錯誤訊息後再試一次")
                        messagebox.showerror("下載失敗", msg[1])

        except queue.Empty:
            pass

        self.after(100, self._poll_queue)


if __name__ == "__main__":
    App().mainloop()
