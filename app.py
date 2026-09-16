# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

import customtkinter as ctk
import requests
from gallery_dl import config, job
from playwright.sync_api import sync_playwright
from tkinter import filedialog, messagebox

APP_NAME = "IG Media Downloader"
APP_VERSION = "1.3.0"
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
VIDEO_EXTS = {"mp4", "mov", "webm", "m4v", "mkv"}


def settings_path() -> Path:
    root = Path(os.environ.get("APPDATA", Path.home())) / "IG-Media-Downloader"
    root.mkdir(parents=True, exist_ok=True)
    return root / "settings.json"


def load_settings() -> dict:
    data = {"folder": str(Path.home() / "Downloads" / "Instagram"), "theme": "System", "cookie": "不使用"}
    try:
        data.update(json.loads(settings_path().read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save_settings(data: dict):
    try:
        settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise ValueError("請貼上 Instagram 連結")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host != "instagram.com" and not host.endswith(".instagram.com"):
        raise ValueError("目前只接受 instagram.com 連結")
    return raw


def target_info(url: str):
    parts = [x for x in urlparse(url).path.split("/") if x]
    if not parts:
        return "unknown", ""
    if parts[0].lower() in {"p", "reel", "tv"}:
        return "post", parts[1] if len(parts) > 1 else ""
    reserved = {"reels", "stories", "explore", "accounts", "direct", "about", "developer", "legal"}
    if parts[0].lower() not in reserved:
        return "profile", parts[0]
    return "unknown", ""


def is_cdn(url: str) -> bool:
    low = (url or "").lower()
    return "cdninstagram.com" in low or "fbcdn.net" in low or "scontent-" in low


def ext_for(content_type: str, url: str) -> str:
    ctype = (content_type or "").split(";", 1)[0].lower().strip()
    known = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "image/avif": ".avif", "video/mp4": ".mp4",
        "video/webm": ".webm", "video/quicktime": ".mov",
    }
    if ctype in known:
        return known[ctype]
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return suffix if 1 < len(suffix) <= 6 else (mimetypes.guess_extension(ctype) or ".bin")


def collect_post_links(page):
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(a => a.href).filter(Boolean)")
    out, seen = [], set()
    for href in hrefs:
        p = urlparse(href)
        if p.netloc.lower().endswith("imginn.com") and re.match(r"^/p/[^/]+/?$", p.path):
            link = "https://imginn.com" + p.path.rstrip("/") + "/"
            if link not in seen:
                seen.add(link)
                out.append(link)
    return out


def collect_media(page):
    rows = page.evaluate("""() => {
      const out=[];
      for (const a of document.querySelectorAll('a[href]')) {
        const t=(a.innerText||a.textContent||'').trim().toLowerCase();
        if (t.includes('download')) out.push({url:a.href,kind:'media'});
      }
      for (const img of document.querySelectorAll('img')) {
        const u=img.currentSrc||img.src||'';
        if (u && ((img.naturalWidth||0)>=500 || (img.naturalHeight||0)>=500)) out.push({url:u,kind:'image'});
      }
      for (const v of document.querySelectorAll('video')) {
        const u=v.currentSrc||v.src||''; if(u) out.push({url:u,kind:'video'});
        for(const s of v.querySelectorAll('source')) if(s.src) out.push({url:s.src,kind:'video'});
      }
      return out;
    }""")
    out, seen = [], set()
    for row in rows:
        u = row.get("url", "")
        if is_cdn(u) and u not in seen:
            seen.add(u)
            out.append({"url": u, "kind": row.get("kind", "media")})
    return out


def verification_page(page) -> bool:
    try:
        text = ((page.title() or "") + " " + page.locator("body").inner_text(timeout=3000)).lower()
    except Exception:
        return False
    return any(x in text for x in ("verify you are human", "security verification", "just a moment", "captcha"))


def expand_profile(page, status, label):
    stagnant = 0
    for _ in range(500):
        before = len(collect_post_links(page))
        status(f"{label}：目前找到 {before} 篇")
        buttons = page.get_by_role("button", name=re.compile(r"^\s*More\s*$", re.I))
        if buttons.count() == 0:
            break
        try:
            b = buttons.last
            if not b.is_visible():
                break
            b.scroll_into_view_if_needed(timeout=5000)
            b.click(timeout=10000)
            page.wait_for_timeout(1200)
        except Exception:
            break
        after = len(collect_post_links(page))
        stagnant = stagnant + 1 if after <= before else 0
        if stagnant >= 3:
            break
    return collect_post_links(page)


def scrape_public_profile(username: str, status):
    browser = None
    try:
        with sync_playwright() as p:
            errors = []
            for channel in ("msedge", "chrome"):
                try:
                    browser = p.chromium.launch(channel=channel, headless=False)
                    break
                except Exception as exc:
                    errors.append(f"{channel}: {exc}")
            if browser is None:
                raise RuntimeError("無法啟動 Edge / Chrome\n" + "\n".join(errors))
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(15000)
            posts, seen_posts = [], set()
            for label, url in (("Posts", f"https://imginn.com/{username}/"), ("Reels", f"https://imginn.com/reels/{username}/")):
                status(f"公開備援：讀取 {label}…")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(1000)
                except Exception:
                    if label == "Posts":
                        raise
                    continue
                if verification_page(page):
                    raise RuntimeError("公開備援網站顯示驗證頁，程式已停止；不會破解 CAPTCHA 或規避網站驗證。")
                for link in expand_profile(page, status, label):
                    if link not in seen_posts:
                        seen_posts.add(link)
                        posts.append(link)
            if not posts:
                raise RuntimeError(f"公開帳號 @{username} 沒有取得貼文連結")
            status(f"共找到 {len(posts)} 篇，開始逐篇解析全部媒體…")
            media, seen = [], set()
            for i, post in enumerate(posts, 1):
                status(f"解析貼文 {i} / {len(posts)}")
                try:
                    page.goto(post, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(500)
                    if verification_page(page):
                        raise RuntimeError("公開備援網站顯示驗證頁，程式已停止。")
                    for item in collect_media(page):
                        if item["url"] not in seen:
                            seen.add(item["url"])
                            media.append(item)
                except RuntimeError:
                    raise
                except Exception:
                    continue
            return media
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


class App(ctk.CTk):
    def __init__(self):
        self.cfg = load_settings()
        ctk.set_appearance_mode(self.cfg["theme"])
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("900x680")
        self.minsize(820, 600)
        self.media = []
        self.backend = ""
        self.username = ""
        self.busy = False
        self._ui()

    def _ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, padx=28, pady=(22, 8), sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Instagram 媒體下載器", font=ctk.CTkFont(size=28, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(top, text="公開帳號免登入 · Post / Reel / Carousel / Profile", text_color=("gray35", "gray70")).grid(row=1, column=0, sticky="w")
        theme = ctk.CTkOptionMenu(top, values=["System", "Light", "Dark"], command=self.change_theme, width=110)
        theme.set(self.cfg["theme"]); theme.grid(row=0, column=1, rowspan=2)

        card = ctk.CTkFrame(self, corner_radius=18); card.grid(row=1, column=0, padx=28, pady=8, sticky="ew"); card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Instagram URL", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, padx=18, pady=(16, 6), sticky="w")
        self.url = ctk.CTkEntry(card, height=44, placeholder_text="https://www.instagram.com/username/")
        self.url.grid(row=1, column=0, padx=(18, 8), pady=(0, 16), sticky="ew"); self.url.bind("<Return>", lambda _e: self.analyze())
        ctk.CTkButton(card, text="貼上", width=90, height=44, command=self.paste).grid(row=1, column=1, padx=4, pady=(0, 16))
        self.analyze_btn = ctk.CTkButton(card, text="解析整頁", width=120, height=44, command=self.analyze)
        self.analyze_btn.grid(row=1, column=2, padx=(4, 18), pady=(0, 16))

        options = ctk.CTkFrame(self, corner_radius=18); options.grid(row=2, column=0, padx=28, pady=8, sticky="ew"); options.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(options, text="下載到", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=(18, 10), pady=(16, 8))
        self.folder = ctk.CTkEntry(options, height=38); self.folder.insert(0, self.cfg["folder"]); self.folder.grid(row=0, column=1, pady=(16, 8), sticky="ew")
        ctk.CTkButton(options, text="選擇資料夾", width=110, command=self.choose_folder).grid(row=0, column=2, padx=(10, 18), pady=(16, 8))
        ctk.CTkLabel(options, text="Cookie（公開內容不用）", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=(18, 10), pady=(8, 16))
        self.cookie = ctk.CTkOptionMenu(options, values=["不使用", "Chrome", "Edge", "Firefox"], width=150)
        self.cookie.set(self.cfg.get("cookie", "不使用")); self.cookie.grid(row=1, column=1, pady=(8, 16), sticky="w")

        stat = ctk.CTkFrame(self, corner_radius=18); stat.grid(row=3, column=0, padx=28, pady=8, sticky="ew"); stat.grid_columnconfigure(1, weight=1)
        self.summary = ctk.CTkLabel(stat, text="尚未解析", font=ctk.CTkFont(size=18, weight="bold")); self.summary.grid(row=0, column=0, padx=18, pady=(14, 4), sticky="w")
        self.progress = ctk.CTkProgressBar(stat, height=10); self.progress.grid(row=0, column=1, padx=18, pady=(18, 4), sticky="ew"); self.progress.set(0)
        self.status = ctk.CTkLabel(stat, text="貼上連結後按解析整頁", text_color=("gray35", "gray70")); self.status.grid(row=1, column=0, columnspan=2, padx=18, pady=(0, 14), sticky="w")

        body = ctk.CTkFrame(self, corner_radius=18); body.grid(row=4, column=0, padx=28, pady=8, sticky="nsew"); body.grid_columnconfigure(0, weight=1); body.grid_rowconfigure(0, weight=1)
        self.log = ctk.CTkTextbox(body, font=ctk.CTkFont(family="Consolas", size=13)); self.log.grid(row=0, column=0, padx=18, pady=16, sticky="nsew")

        bottom = ctk.CTkFrame(self, fg_color="transparent"); bottom.grid(row=5, column=0, padx=28, pady=(8, 22), sticky="ew"); bottom.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bottom, text="遇到驗證會停止，不破解 CAPTCHA 或規避存取限制。", text_color=("gray40", "gray65")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="開啟資料夾", width=110, command=self.open_folder).grid(row=0, column=1, padx=8)
        self.download_btn = ctk.CTkButton(bottom, text="全部下載", width=140, command=self.download, state="disabled")
        self.download_btn.grid(row=0, column=2)

    def change_theme(self, value):
        ctk.set_appearance_mode(value); self.cfg["theme"] = value; save_settings(self.cfg)

    def paste(self):
        try:
            self.url.delete(0, "end"); self.url.insert(0, self.clipboard_get().strip())
        except Exception:
            pass

    def choose_folder(self):
        p = filedialog.askdirectory(initialdir=self.folder.get() or str(Path.home()))
        if p:
            self.folder.delete(0, "end"); self.folder.insert(0, p); self.cfg["folder"] = p; save_settings(self.cfg)

    def set_busy(self, value):
        self.busy = value; self.analyze_btn.configure(state="disabled" if value else "normal")
        self.download_btn.configure(state="normal" if (not value and self.media) else "disabled")

    def set_status(self, text):
        self.after(0, lambda: self.status.configure(text=text[:180]))

    def gallery_config(self):
        out = Path(self.folder.get().strip()).expanduser(); out.mkdir(parents=True, exist_ok=True)
        config.set((), "base-directory", str(out)); config.set((), "directory", ()); config.set((), "retries", 3)
        mode = self.cookie.get(); config.set((), "cookies", None if mode == "不使用" else [mode.lower()])
        self.cfg.update({"folder": str(out), "cookie": mode}); save_settings(self.cfg)
        return out

    def analyze(self):
        if self.busy: return
        try:
            url = normalize_url(self.url.get()); self.gallery_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc)); return
        self.media = []; self.backend = ""; self.progress.set(0); self.summary.configure(text="正在解析整頁…"); self.set_busy(True)
        threading.Thread(target=self._analyze_worker, args=(url,), daemon=True).start()

    def _analyze_worker(self, url):
        direct_error = None
        try:
            d = job.DataJob(url, file=None, resolve=True); d.run()
            if d.exception: raise d.exception
            out, seen = [], set()
            for i, u in enumerate(d.data_urls):
                meta = d.data_meta[i] if i < len(d.data_meta) else {}
                ext = str(meta.get("extension") or "").lower().lstrip(".") or Path(urlparse(u).path).suffix.lower().lstrip(".")
                if ext in IMAGE_EXTS | VIDEO_EXTS and u not in seen:
                    seen.add(u); out.append({"url": u, "kind": "video" if ext in VIDEO_EXTS else "image", "meta": meta})
            if out:
                self.backend = "gallery"; self.media = out; self.after(0, self._analyze_done); return
        except Exception as exc:
            direct_error = exc
        kind, username = target_info(url)
        if kind != "profile" or not username:
            self.after(0, lambda: self.fail("解析失敗", direct_error or RuntimeError("沒有解析到媒體"))); return
        try:
            self.username = username; self.set_status(f"直連無結果，公開備援解析 @{username}…")
            self.media = scrape_public_profile(username, self.set_status)
            if not self.media: raise RuntimeError("公開備援也沒有解析到媒體")
            self.backend = "imginn"; self.after(0, self._analyze_done)
        except Exception as exc:
            self.after(0, lambda e=exc: self.fail("解析失敗", e))

    def _analyze_done(self):
        images = sum(x.get("kind") == "image" for x in self.media); videos = sum(x.get("kind") == "video" for x in self.media)
        self.summary.configure(text=f"已解析 {len(self.media)} 個媒體")
        self.status.configure(text=f"照片 {images} · 影片 {videos} · {'Instagram 直連' if self.backend == 'gallery' else '公開帳號備援'}")
        self.log.delete("1.0", "end")
        for i, x in enumerate(self.media, 1): self.log.insert("end", f"{i:04d}  {'影片' if x.get('kind') == 'video' else '照片'}\n")
        self.progress.set(1); self.set_busy(False)

    def download(self):
        if self.busy or not self.media: return
        try: out = self.gallery_config()
        except Exception as exc: messagebox.showerror(APP_NAME, str(exc)); return
        self.progress.set(0); self.set_busy(True); threading.Thread(target=self._download_worker, args=(out,), daemon=True).start()

    def _download_worker(self, out):
        try:
            if self.backend == "gallery":
                j = job.DownloadJob(normalize_url(self.url.get())); code = j.run()
                if code: raise RuntimeError(f"下載器回傳狀態碼 {code}")
            else:
                s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://imginn.com/", "Accept": "*/*"})
                total = len(self.media)
                for i, item in enumerate(self.media, 1):
                    self.set_status(f"下載 {i} / {total}"); self.after(0, lambda n=i, t=total: self.progress.set(n / t))
                    r = s.get(item["url"], stream=True, timeout=90, allow_redirects=True); r.raise_for_status()
                    if "text/html" in r.headers.get("Content-Type", "").lower(): raise RuntimeError("媒體網址回傳 HTML")
                    path = out / f"{self.username}_{i:05d}{ext_for(r.headers.get('Content-Type',''), item['url'])}"
                    if path.exists() and path.stat().st_size > 0: continue
                    tmp = Path(str(path) + ".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1024 * 1024):
                            if chunk: f.write(chunk)
                    tmp.replace(path)
            self.after(0, self.download_done)
        except Exception as exc:
            self.after(0, lambda e=exc: self.fail("下載失敗", e))

    def download_done(self):
        self.progress.set(1); self.summary.configure(text="下載完成"); self.status.configure(text=self.folder.get()); self.set_busy(False); messagebox.showinfo(APP_NAME, "全部下載完成")

    def fail(self, title, exc):
        text = str(exc).strip() or exc.__class__.__name__
        self.summary.configure(text=title); self.status.configure(text=text.splitlines()[0][:180]); self.progress.set(0); self.set_busy(False)
        self.log.delete("1.0", "end"); self.log.insert("1.0", f"{title}\n\n{text}")
        messagebox.showerror(APP_NAME, text)

    def open_folder(self):
        p = Path(self.folder.get()).expanduser(); p.mkdir(parents=True, exist_ok=True)
        try: os.startfile(p)
        except AttributeError: subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])


if __name__ == "__main__":
    App().mainloop()
