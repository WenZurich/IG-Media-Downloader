# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

import customtkinter as ctk
import requests
from PIL import Image, ImageOps
from gallery_dl import config, job
from playwright.sync_api import sync_playwright
from tkinter import filedialog, messagebox

APP_NAME = "IG Media Downloader"
APP_VERSION = "1.4.0"
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
VIDEO_EXTS = {"mp4", "mov", "webm", "m4v", "mkv"}
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)


def settings_path() -> Path:
    root = Path(os.environ.get("APPDATA", Path.home())) / "IG-Media-Downloader"
    root.mkdir(parents=True, exist_ok=True)
    return root / "settings.json"


def load_settings() -> dict:
    data = {
        "folder": str(Path.home() / "Downloads" / "Instagram"),
        "theme": "System",
        "cookie": "不使用",
    }
    try:
        data.update(json.loads(settings_path().read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save_settings(data: dict):
    try:
        settings_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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
    reserved = {
        "reels", "stories", "explore", "accounts", "direct",
        "about", "developer", "legal",
    }
    if parts[0].lower() not in reserved:
        return "profile", parts[0]
    return "unknown", ""


def is_cdn(url: str) -> bool:
    low = (url or "").lower()
    return (
        "cdninstagram.com" in low
        or "fbcdn.net" in low
        or "scontent-" in low
    )


def ext_for(content_type: str, url: str) -> str:
    ctype = (content_type or "").split(";", 1)[0].lower().strip()
    known = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    }
    if ctype in known:
        return known[ctype]
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return suffix if 1 < len(suffix) <= 6 else (mimetypes.guess_extension(ctype) or ".bin")


def kind_for(content_type: str, url: str, hint: str = "") -> str:
    ctype = (content_type or "").split(";", 1)[0].lower().strip()
    if ctype.startswith("image/"):
        return "image"
    if ctype.startswith("video/"):
        return "video"
    ext = ext_for(content_type, url).lower().lstrip(".")
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return hint if hint in {"image", "video"} else "media"


def collect_post_links(page):
    hrefs = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(a => a.href).filter(Boolean)",
    )
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
    rows = page.evaluate(
        """() => {
          const out=[];
          for (const a of document.querySelectorAll('a[href]')) {
            const t=(a.innerText||a.textContent||'').trim().toLowerCase();
            if (t.includes('download')) out.push({url:a.href,kind:'media'});
          }
          for (const img of document.querySelectorAll('img')) {
            const u=img.currentSrc||img.src||'';
            if (u && ((img.naturalWidth||0)>=500 || (img.naturalHeight||0)>=500))
              out.push({url:u,kind:'image'});
          }
          for (const v of document.querySelectorAll('video')) {
            const u=v.currentSrc||v.src||'';
            if(u) out.push({url:u,kind:'video'});
            for(const s of v.querySelectorAll('source'))
              if(s.src) out.push({url:s.src,kind:'video'});
          }
          return out;
        }"""
    )
    out, seen = [], set()
    for row in rows:
        u = row.get("url", "")
        if is_cdn(u) and u not in seen:
            seen.add(u)
            out.append({"url": u, "kind": row.get("kind", "media")})
    return out


def verification_page(page) -> bool:
    try:
        text = (
            (page.title() or "")
            + " "
            + page.locator("body").inner_text(timeout=3000)
        ).lower()
    except Exception:
        return False
    return any(
        x in text
        for x in (
            "verify you are human",
            "security verification",
            "just a moment",
            "captcha",
        )
    )


def expand_profile(page, status, label):
    stagnant = 0
    for _ in range(500):
        before = len(collect_post_links(page))
        status(f"{label}：目前找到 {before} 篇")
        buttons = page.get_by_role(
            "button",
            name=re.compile(r"^\s*More\s*$", re.I),
        )
        if buttons.count() == 0:
            break
        try:
            b = buttons.last
            if not b.is_visible():
                break
            b.scroll_into_view_if_needed(timeout=5000)
            b.click(timeout=10000)
            page.wait_for_timeout(1000)
        except Exception:
            break
        after = len(collect_post_links(page))
        stagnant = stagnant + 1 if after <= before else 0
        if stagnant >= 3:
            break
    return collect_post_links(page)


def save_response_bytes(data: bytes, content_type: str, url: str, cache_dir: Path, index: int) -> tuple[Path, str]:
    kind = kind_for(content_type, url)
    if kind not in {"image", "video"}:
        raise RuntimeError("不是可辨識的照片或影片")
    ext = ext_for(content_type, url)
    path = cache_dir / f"{index:05d}{ext}"
    path.write_bytes(data)
    return path, kind


def cache_with_browser(page, media_url: str, post_url: str, cache_dir: Path, index: int, hint: str):
    try:
        response = page.context.request.get(
            media_url,
            headers={
                "Referer": post_url,
                "User-Agent": BROWSER_UA,
                "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
            },
            timeout=90000,
        )
        if response.ok:
            content_type = response.headers.get("content-type", "")
            data = response.body()
            path, real_kind = save_response_bytes(
                data, content_type, media_url, cache_dir, index
            )
            return {
                "url": media_url,
                "kind": real_kind if real_kind != "media" else hint,
                "local_path": str(path),
                "post_url": post_url,
            }
    except Exception:
        pass

    response = requests.get(
        media_url,
        headers={
            "User-Agent": BROWSER_UA,
            "Referer": post_url,
            "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
        },
        timeout=90,
        allow_redirects=True,
    )
    response.raise_for_status()
    if "text/html" in response.headers.get("Content-Type", "").lower():
        raise RuntimeError("媒體網址回傳 HTML")
    path, real_kind = save_response_bytes(
        response.content,
        response.headers.get("Content-Type", ""),
        media_url,
        cache_dir,
        index,
    )
    return {
        "url": media_url,
        "kind": real_kind if real_kind != "media" else hint,
        "local_path": str(path),
        "post_url": post_url,
    }


def scrape_public_profile(username: str, status, cache_dir: Path):
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
            for label, url in (
                ("Posts", f"https://imginn.com/{username}/"),
                ("Reels", f"https://imginn.com/reels/{username}/"),
            ):
                status(f"公開備援：讀取 {label}…")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(900)
                except Exception:
                    if label == "Posts":
                        raise
                    continue

                if verification_page(page):
                    raise RuntimeError(
                        "公開備援網站顯示驗證頁，程式已停止；"
                        "不會破解 CAPTCHA 或規避網站驗證。"
                    )

                for link in expand_profile(page, status, label):
                    if link not in seen_posts:
                        seen_posts.add(link)
                        posts.append(link)

            if not posts:
                raise RuntimeError(f"公開帳號 @{username} 沒有取得貼文連結")

            status(f"共找到 {len(posts)} 篇，開始逐篇解析並建立高清預覽…")
            media, seen_urls = [], set()
            saved_index = 0

            for post_i, post in enumerate(posts, 1):
                status(f"解析貼文 {post_i} / {len(posts)} · 建立高清預覽")
                try:
                    page.goto(post, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(450)

                    if verification_page(page):
                        raise RuntimeError("公開備援網站顯示驗證頁，程式已停止。")

                    for raw in collect_media(page):
                        media_url = raw["url"]
                        if media_url in seen_urls:
                            continue
                        seen_urls.add(media_url)

                        try:
                            saved_index += 1
                            item = cache_with_browser(
                                page,
                                media_url,
                                post,
                                cache_dir,
                                saved_index,
                                raw.get("kind", "media"),
                            )
                            media.append(item)
                        except Exception:
                            saved_index -= 1
                            continue
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


def cache_direct_item(item: dict, cache_dir: Path, index: int):
    url = item["url"]
    response = requests.get(
        url,
        headers={
            "User-Agent": BROWSER_UA,
            "Referer": "https://www.instagram.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
        },
        timeout=90,
        allow_redirects=True,
    )
    response.raise_for_status()
    if "text/html" in response.headers.get("Content-Type", "").lower():
        return item
    kind = kind_for(
        response.headers.get("Content-Type", ""),
        url,
        item.get("kind", ""),
    )
    if kind not in {"image", "video"}:
        return item
    ext = ext_for(response.headers.get("Content-Type", ""), url)
    path = cache_dir / f"{index:05d}{ext}"
    path.write_bytes(response.content)
    item = dict(item)
    item["kind"] = kind
    item["local_path"] = str(path)
    return item


class App(ctk.CTk):
    def __init__(self):
        self.cfg = load_settings()
        ctk.set_appearance_mode(self.cfg["theme"])
        ctk.set_default_color_theme("blue")

        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1120x820")
        self.minsize(900, 680)

        self.media = []
        self.backend = ""
        self.username = ""
        self.busy = False
        self.thumb_refs = []
        self.preview_window = None
        self.cache_dir = Path(tempfile.mkdtemp(prefix="ig_media_downloader_"))

        self._ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, padx=28, pady=(22, 8), sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top,
            text="Instagram 媒體下載器",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            top,
            text="公開帳號免登入 · 高清預覽 · Post / Reel / Carousel / Profile",
            text_color=("gray35", "gray70"),
        ).grid(row=1, column=0, sticky="w")

        theme = ctk.CTkOptionMenu(
            top,
            values=["System", "Light", "Dark"],
            command=self.change_theme,
            width=110,
        )
        theme.set(self.cfg["theme"])
        theme.grid(row=0, column=1, rowspan=2)

        card = ctk.CTkFrame(self, corner_radius=18)
        card.grid(row=1, column=0, padx=28, pady=8, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="Instagram URL",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, columnspan=3, padx=18, pady=(16, 6), sticky="w")

        self.url = ctk.CTkEntry(
            card,
            height=44,
            placeholder_text="https://www.instagram.com/username/",
        )
        self.url.grid(row=1, column=0, padx=(18, 8), pady=(0, 16), sticky="ew")
        self.url.bind("<Return>", lambda _e: self.analyze())

        ctk.CTkButton(card, text="貼上", width=90, height=44, command=self.paste).grid(
            row=1, column=1, padx=4, pady=(0, 16)
        )

        self.analyze_btn = ctk.CTkButton(
            card, text="解析整頁", width=120, height=44, command=self.analyze
        )
        self.analyze_btn.grid(row=1, column=2, padx=(4, 18), pady=(0, 16))

        options = ctk.CTkFrame(self, corner_radius=18)
        options.grid(row=2, column=0, padx=28, pady=8, sticky="ew")
        options.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(options, text="下載到", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=(18, 10), pady=(16, 8)
        )

        self.folder = ctk.CTkEntry(options, height=38)
        self.folder.insert(0, self.cfg["folder"])
        self.folder.grid(row=0, column=1, pady=(16, 8), sticky="ew")

        ctk.CTkButton(options, text="選擇資料夾", width=110, command=self.choose_folder).grid(
            row=0, column=2, padx=(10, 18), pady=(16, 8)
        )

        ctk.CTkLabel(
            options,
            text="Cookie（公開內容不用）",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=1, column=0, padx=(18, 10), pady=(8, 16))

        self.cookie = ctk.CTkOptionMenu(
            options, values=["不使用", "Chrome", "Edge", "Firefox"], width=150
        )
        self.cookie.set(self.cfg.get("cookie", "不使用"))
        self.cookie.grid(row=1, column=1, pady=(8, 16), sticky="w")

        stat = ctk.CTkFrame(self, corner_radius=18)
        stat.grid(row=3, column=0, padx=28, pady=8, sticky="ew")
        stat.grid_columnconfigure(1, weight=1)

        self.summary = ctk.CTkLabel(
            stat, text="尚未解析", font=ctk.CTkFont(size=18, weight="bold")
        )
        self.summary.grid(row=0, column=0, padx=18, pady=(14, 4), sticky="w")

        self.progress = ctk.CTkProgressBar(stat, height=10)
        self.progress.grid(row=0, column=1, padx=18, pady=(18, 4), sticky="ew")
        self.progress.set(0)

        self.status = ctk.CTkLabel(
            stat,
            text="貼上連結後按解析整頁",
            text_color=("gray35", "gray70"),
        )
        self.status.grid(row=1, column=0, columnspan=2, padx=18, pady=(0, 14), sticky="w")

        body = ctk.CTkFrame(self, corner_radius=18)
        body.grid(row=4, column=0, padx=28, pady=8, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            body,
            text="高清預覽  ·  滑鼠滾輪 / 右側滑軌",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(14, 4), sticky="w")

        self.preview = ctk.CTkScrollableFrame(
            body,
            corner_radius=12,
            fg_color=("gray92", "gray14"),
        )
        self.preview.grid(row=1, column=0, padx=18, pady=(6, 16), sticky="nsew")
        for col in range(3):
            self.preview.grid_columnconfigure(col, weight=1, uniform="preview")

        try:
            self.preview._scrollbar.configure(width=18)
            self.preview._parent_canvas.bind("<MouseWheel>", self._on_preview_mousewheel)
        except Exception:
            pass

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=5, column=0, padx=28, pady=(8, 22), sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            bottom,
            text="解析時先快取可下載媒體，避免按「全部下載」後 CDN 網址過期。",
            text_color=("gray40", "gray65"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(bottom, text="開啟資料夾", width=110, command=self.open_folder).grid(
            row=0, column=1, padx=8
        )

        self.download_btn = ctk.CTkButton(
            bottom, text="全部下載", width=140, command=self.download, state="disabled"
        )
        self.download_btn.grid(row=0, column=2)

        self._show_preview_message("解析完成後會在這裡顯示高清縮圖。")

    def _on_preview_mousewheel(self, event):
        try:
            direction = -1 if event.delta > 0 else 1
            for step in range(1, 6):
                self.after(
                    step * 10,
                    lambda d=direction: self.preview._parent_canvas.yview_scroll(d, "units"),
                )
            return "break"
        except Exception:
            return None

    def _clear_cache(self):
        try:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
        except Exception:
            pass
        self.cache_dir = Path(tempfile.mkdtemp(prefix="ig_media_downloader_"))

    def _clear_preview(self):
        self.thumb_refs.clear()
        for child in self.preview.winfo_children():
            child.destroy()

    def _show_preview_message(self, text):
        self._clear_preview()
        ctk.CTkLabel(
            self.preview,
            text=text,
            text_color=("gray35", "gray70"),
            font=ctk.CTkFont(size=14),
        ).grid(row=0, column=0, columnspan=3, padx=20, pady=30)

    def change_theme(self, value):
        ctk.set_appearance_mode(value)
        self.cfg["theme"] = value
        save_settings(self.cfg)

    def paste(self):
        try:
            self.url.delete(0, "end")
            self.url.insert(0, self.clipboard_get().strip())
        except Exception:
            pass

    def choose_folder(self):
        p = filedialog.askdirectory(initialdir=self.folder.get() or str(Path.home()))
        if p:
            self.folder.delete(0, "end")
            self.folder.insert(0, p)
            self.cfg["folder"] = p
            save_settings(self.cfg)

    def set_busy(self, value):
        self.busy = value
        self.analyze_btn.configure(state="disabled" if value else "normal")
        self.download_btn.configure(
            state="normal" if (not value and self.media) else "disabled"
        )

    def set_status(self, text):
        self.after(0, lambda t=text: self.status.configure(text=t[:180]))

    def gallery_config(self):
        out = Path(self.folder.get().strip()).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        config.set((), "base-directory", str(out))
        config.set((), "directory", ())
        config.set((), "retries", 3)

        mode = self.cookie.get()
        config.set((), "cookies", None if mode == "不使用" else [mode.lower()])

        self.cfg.update({"folder": str(out), "cookie": mode})
        save_settings(self.cfg)
        return out

    def analyze(self):
        if self.busy:
            return
        try:
            url = normalize_url(self.url.get())
            self.gallery_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        self._clear_cache()
        self.media = []
        self.backend = ""
        kind, ident = target_info(url)
        self.username = ident if kind == "profile" else ""

        self.progress.set(0)
        self.summary.configure(text="正在解析整頁…")
        self.status.configure(text="先解析，再建立高清預覽快取…")
        self._show_preview_message("正在解析，請稍候…")
        self.set_busy(True)

        threading.Thread(target=self._analyze_worker, args=(url,), daemon=True).start()

    def _analyze_worker(self, url):
        direct_error = None

        try:
            d = job.DataJob(url, file=None, resolve=True)
            d.run()
            if d.exception:
                raise d.exception

            out, seen = [], set()
            for i, u in enumerate(d.data_urls):
                meta = d.data_meta[i] if i < len(d.data_meta) else {}
                ext = (
                    str(meta.get("extension") or "").lower().lstrip(".")
                    or Path(urlparse(u).path).suffix.lower().lstrip(".")
                )
                if ext in IMAGE_EXTS | VIDEO_EXTS and u not in seen:
                    seen.add(u)
                    out.append(
                        {
                            "url": u,
                            "kind": "video" if ext in VIDEO_EXTS else "image",
                            "meta": meta,
                        }
                    )

            if out:
                self.set_status(
                    f"Instagram 直連解析到 {len(out)} 個媒體，建立預覽快取…"
                )
                cached = []
                for i, item in enumerate(out, 1):
                    try:
                        cached.append(cache_direct_item(item, self.cache_dir, i))
                    except Exception:
                        cached.append(item)
                    self.after(
                        0,
                        lambda n=i, t=len(out): self.progress.set(n / max(1, t)),
                    )

                self.backend = "gallery"
                self.media = cached
                self.after(0, self._analyze_done)
                return
        except Exception as exc:
            direct_error = exc

        kind, username = target_info(url)
        if kind != "profile" or not username:
            self.after(
                0,
                lambda: self.fail(
                    "解析失敗",
                    direct_error or RuntimeError("沒有解析到媒體"),
                ),
            )
            return

        try:
            self.username = username
            self.set_status(f"直連無結果，公開備援解析 @{username}…")
            self.media = scrape_public_profile(username, self.set_status, self.cache_dir)
            if not self.media:
                raise RuntimeError("公開備援也沒有解析到可下載媒體")

            self.backend = "imginn"
            self.after(0, self._analyze_done)
        except Exception as exc:
            self.after(0, lambda e=exc: self.fail("解析失敗", e))

    def _analyze_done(self):
        images = sum(x.get("kind") == "image" for x in self.media)
        videos = sum(x.get("kind") == "video" for x in self.media)
        cached = sum(bool(x.get("local_path")) for x in self.media)

        self.summary.configure(text=f"已解析 {len(self.media)} 個媒體")
        self.status.configure(
            text=(
                f"照片 {images} · 影片 {videos} · "
                f"高清快取 {cached}/{len(self.media)} · "
                f"{'Instagram 直連' if self.backend == 'gallery' else '公開帳號備援'}"
            )
        )
        self.progress.set(1)
        self.set_busy(False)
        self._render_preview()

    def _render_preview(self):
        self._clear_preview()
        if not self.media:
            self._show_preview_message("沒有可預覽媒體。")
            return
        self._render_preview_batch(0)

    def _render_preview_batch(self, start):
        end = min(start + 6, len(self.media))
        for i in range(start, end):
            self._make_preview_card(i, self.media[i])
        if end < len(self.media):
            self.after(15, lambda e=end: self._render_preview_batch(e))

    def _make_preview_card(self, index, item):
        row = index // 3
        col = index % 3

        card = ctk.CTkFrame(self.preview, corner_radius=14)
        card.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)

        local_path = item.get("local_path")
        kind = item.get("kind", "media")

        if kind == "image" and local_path and Path(local_path).exists():
            try:
                with Image.open(local_path) as original:
                    img = ImageOps.exif_transpose(original).convert("RGB")
                    img.thumbnail((300, 300), Image.Resampling.LANCZOS)
                    display = img.copy()

                ctk_img = ctk.CTkImage(
                    light_image=display,
                    dark_image=display,
                    size=display.size,
                )
                self.thumb_refs.append(ctk_img)

                button = ctk.CTkButton(
                    card,
                    text="",
                    image=ctk_img,
                    fg_color="transparent",
                    hover_color=("gray85", "gray22"),
                    command=lambda p=local_path, n=index + 1: self.open_image_preview(p, n),
                )
                button.grid(row=0, column=0, padx=8, pady=(8, 4), sticky="nsew")
            except Exception:
                self._placeholder(card, "照片", index)
        elif kind == "video":
            self._video_placeholder(card, local_path, index)
        else:
            self._placeholder(card, "媒體", index)

        ctk.CTkLabel(
            card,
            text=f"{index + 1:04d}  {'影片' if kind == 'video' else '照片' if kind == 'image' else '媒體'}",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=1, column=0, padx=10, pady=(2, 10))

    def _placeholder(self, card, text, index):
        ctk.CTkButton(
            card,
            text=f"{text}\n\n預覽暫不可用",
            height=220,
            fg_color=("gray82", "gray24"),
            hover=False,
            state="disabled",
        ).grid(row=0, column=0, padx=8, pady=(8, 4), sticky="nsew")

    def _video_placeholder(self, card, local_path, index):
        button = ctk.CTkButton(
            card,
            text="▶\n\n影片\n點擊播放",
            height=220,
            font=ctk.CTkFont(size=18, weight="bold"),
            command=(
                (lambda p=local_path: self.open_local_file(p))
                if local_path and Path(local_path).exists()
                else None
            ),
            state=(
                "normal"
                if local_path and Path(local_path).exists()
                else "disabled"
            ),
        )
        button.grid(row=0, column=0, padx=8, pady=(8, 4), sticky="nsew")

    def open_image_preview(self, path, number):
        if not Path(path).exists():
            return

        try:
            with Image.open(path) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")

            screen_w = max(900, self.winfo_screenwidth())
            screen_h = max(700, self.winfo_screenheight())
            max_w = min(1400, screen_w - 160)
            max_h = min(900, screen_h - 180)

            image.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)

            win = ctk.CTkToplevel(self)
            win.title(f"高清預覽 #{number:04d}")
            win.geometry(
                f"{min(max_w + 60, screen_w - 80)}x{min(max_h + 100, screen_h - 100)}"
            )
            win.transient(self)

            img_ref = ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=image.size,
            )
            win._preview_image_ref = img_ref

            label = ctk.CTkLabel(win, text="", image=img_ref)
            label.pack(expand=True, fill="both", padx=20, pady=(20, 8))

            ctk.CTkButton(
                win,
                text="用系統程式開啟原始檔",
                command=lambda p=path: self.open_local_file(p),
            ).pack(pady=(0, 16))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"無法開啟預覽：{exc}")

    def open_local_file(self, path):
        try:
            os.startfile(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def download(self):
        if self.busy or not self.media:
            return

        try:
            out = self.gallery_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        self.progress.set(0)
        self.summary.configure(text="正在下載…")
        self.set_busy(True)

        threading.Thread(target=self._download_worker, args=(out,), daemon=True).start()

    def _download_worker(self, out):
        try:
            local_items = [
                item
                for item in self.media
                if item.get("local_path") and Path(item["local_path"]).exists()
            ]

            if self.backend == "imginn":
                if not local_items:
                    raise RuntimeError("沒有有效的預覽快取可下載，請重新解析後再試。")
                self._copy_cached_items(out)
            else:
                if len(local_items) == len(self.media):
                    self._copy_cached_items(out)
                else:
                    j = job.DownloadJob(normalize_url(self.url.get()))
                    code = j.run()
                    if code:
                        raise RuntimeError(f"下載器回傳狀態碼 {code}")

            self.after(0, self.download_done)
        except Exception as exc:
            self.after(0, lambda e=exc: self.fail("下載失敗", e))

    def _copy_cached_items(self, out: Path):
        total = len(self.media)
        prefix = self.username or "instagram"

        for i, item in enumerate(self.media, 1):
            local = item.get("local_path")
            if not local or not Path(local).exists():
                raise RuntimeError(f"第 {i} 個媒體的快取已失效，請重新解析。")

            source = Path(local)
            destination = out / f"{prefix}_{i:05d}{source.suffix.lower()}"

            if destination.exists():
                stem = destination.stem
                suffix = destination.suffix
                n = 2
                while destination.exists():
                    destination = out / f"{stem}_{n}{suffix}"
                    n += 1

            shutil.copy2(source, destination)

            self.set_status(f"下載 {i} / {total} · 複製高清快取")
            self.after(
                0,
                lambda n=i, t=total: self.progress.set(n / max(1, t)),
            )

    def download_done(self):
        self.progress.set(1)
        self.summary.configure(text="下載完成")
        self.status.configure(text=self.folder.get())
        self.set_busy(False)
        messagebox.showinfo(APP_NAME, "全部下載完成")

    def fail(self, title, exc):
        text = str(exc).strip() or exc.__class__.__name__
        self.summary.configure(text=title)
        self.status.configure(text=text.splitlines()[0][:180])
        self.progress.set(0)
        self.set_busy(False)
        messagebox.showerror(APP_NAME, text)

    def open_folder(self):
        p = Path(self.folder.get()).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(p)
        except AttributeError:
            subprocess.Popen(
                ["open" if sys.platform == "darwin" else "xdg-open", str(p)]
            )

    def on_close(self):
        try:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
