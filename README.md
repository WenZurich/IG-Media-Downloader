# IG Media Downloader

Windows EXE for downloading all accessible photos/videos from an Instagram URL.

## Download

After the GitHub Actions build succeeds, the newest executable is always published here:

https://github.com/WenZurich/IG-Media-Downloader/releases/download/latest/IG-Media-Downloader.exe

## Current version

v1.3.0

- Post / Reel / Carousel support
- Public Profile support
- Public content is tried without Instagram login first
- For public profile URLs that Instagram does not expose directly, the app can fall back to the public Imginn view using the locally installed Microsoft Edge or Chrome
- The fallback loads available Posts/Reels, visits each public post, and collects all visible high-resolution media
- Selectable download folder
- Light / Dark / System UI
- Optional own-browser cookie mode for content the user is legitimately authorized to view

## Safety / access behavior

The app does not solve CAPTCHAs, use stealth browser plugins, rotate proxies, spoof browser fingerprints, or bypass access controls. If a site presents a verification page, the app stops and reports it.

Only download content you own, have permission to save, or are otherwise legally entitled to download.

## Build

Every push to `main` automatically builds `IG-Media-Downloader.exe` on a Windows GitHub Actions runner and replaces the asset in the stable `latest` release.

The workflow artifact is also retained in the Actions run.
