"""
movie_downloader.py
────────────────────
Downloads movies from @cinemagic_hd_bot using Playwright browser automation.

Strategy:
1. Open Telegram Web in a persistent browser session (so you stay logged in)
2. Navigate to @cinemagic_hd_bot and open its Mini App (the streaming web app)
3. Use the Search tab to find the movie
4. Intercept network requests to find the actual Telegram file URL
5. Download the file directly

FIRST-TIME SETUP:
    Run: python pipeline/movie_downloader.py --setup
    This opens a real browser window for you to log into Telegram Web.
    Your session is saved and reused forever after.
"""

import asyncio
import os
import sys
import re
import time
import argparse
import requests
from pathlib import Path
from playwright.async_api import async_playwright, Page, BrowserContext
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

TELEGRAM_WEB_URL = "https://web.telegram.org/k/"
SESSION_DIR = config.TELEGRAM_SESSION_DIR


async def setup_session():
    """
    First-time setup: opens a real browser so you can log into Telegram Web.
    Your session cookies are saved to SESSION_DIR and reused every future run.
    """
    console.print("[bold cyan]🔐 FIRST-TIME SETUP — Telegram Login[/bold cyan]")
    console.print("A browser window will open. Log into Telegram Web, then close the browser.")
    console.print(f"Session will be saved to: {SESSION_DIR}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            SESSION_DIR,
            headless=False,  # Must be visible for login
            viewport={"width": 1280, "height": 800},
        )
        page = await browser.new_page()
        await page.goto(TELEGRAM_WEB_URL)
        console.print("[yellow]📱 Log into Telegram Web now. Close the browser window when done.[/yellow]")

        # Wait until user closes the browser
        try:
            await browser.wait_for_event("close", timeout=300_000)
        except Exception:
            pass

        console.print("[green]✅ Session saved! You can now run the pipeline.[/green]")


async def intercept_and_download(page: Page, movie_title: str, output_path: str) -> bool:
    """
    Intercepts network calls from the CinemagicHD mini app to find the video file URL.
    Returns True if download succeeded.
    """
    downloaded_path = None
    download_url = None

    # Set up request interception to catch video file URLs
    captured_urls = []

    async def handle_response(response):
        url = response.url
        # Look for video file responses (Telegram CDN or direct mp4 links)
        if any(ext in url.lower() for ext in [".mp4", ".mkv", ".avi"]):
            captured_urls.append(url)
            console.print(f"[green]🎬 Captured video URL: {url[:80]}...[/green]")
        # Also capture Telegram file API responses
        if "tg://resolve" in url or "getFile" in url.lower():
            captured_urls.append(url)

    page.on("response", handle_response)

    # Also intercept downloads
    async def handle_download(download):
        nonlocal downloaded_path
        save_path = output_path
        await download.save_as(save_path)
        downloaded_path = save_path
        console.print(f"[green]✅ Downloaded to: {save_path}[/green]")

    page.on("download", handle_download)

    return downloaded_path is not None or bool(captured_urls), download_url


async def navigate_to_mini_app(page: Page) -> bool:
    """Navigate to the CinemagicHD bot and open its Mini App."""
    console.print("[cyan]🌐 Navigating to Telegram Web...[/cyan]")
    await page.goto(f"{TELEGRAM_WEB_URL}#@cinemagic_hd_bot", wait_until="networkidle", timeout=30_000)
    await page.wait_for_timeout(5000)

    # Look for a "Launch App" or web app button in the chat
    console.print("[cyan]🚀 Opening CinemagicHD Mini App...[/cyan]")

    try:
        # Send /start to wake up the bot
        chat_input = page.locator('.input-message-input, [contenteditable="true"]').first
        await chat_input.click(timeout=3000)
        await chat_input.fill('/start')
        await page.keyboard.press('Enter')
        await page.wait_for_timeout(3000)
        
        # Use direct Javascript DOM evaluation to find and click the exact button.
        # This bypasses Playwright's strict visibility and animation checks which fail on Telegram's complex UI.
        clicked = await page.evaluate("""() => {
            let btns = Array.from(document.querySelectorAll('button, .reply-markup-button, [role="button"], .bot-menu-button, .chat-input-control-button'));
            let target = btns.find(b => {
                let text = (b.innerText || "").toLowerCase();
                return text.includes("mini app") || text.includes("don't click");
            });
            if(target) {
                target.click();
                return true;
            }
            return false;
        }""")
        
        if not clicked:
            console.print("[red]❌ Could not find the Mini App button in the DOM.[/red]")
            return False
            
        await page.wait_for_timeout(5000)

    except Exception as e:
        console.print(f"[red]❌ Error trying to click Mini App: {e}[/red]")
        return False


    # Confirm the mini app iframe/modal loaded
    try:
        mini_app_frame = page.frame_locator('iframe').first
        await mini_app_frame.locator('body').wait_for(timeout=10_000)
        console.print("[green]✅ CinemagicHD Mini App opened![/green]")
        return True
    except Exception:
        # Maybe no iframe — might be same-page web app
        console.print("[yellow]ℹ️  Mini app may be embedded differently, continuing...[/yellow]")
        return True


async def search_and_download_movie(movie_title: str, output_path: str) -> bool:
    """
    Full automation: open Telegram Web → CinemagicHD → search → download.
    Returns True if successfully downloaded.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if not os.path.exists(SESSION_DIR):
        console.print("[red]❌ No Telegram session found. Run setup first:[/red]")
        console.print("   python pipeline/movie_downloader.py --setup")
        return False

    async with async_playwright() as p:
        # Use the persistent session (stay logged in)
        context: BrowserContext = await p.chromium.launch_persistent_context(
            SESSION_DIR,
            headless=False,  # Keep visible so you can intervene if needed
            viewport={"width": 1280, "height": 900},
            accept_downloads=True,
        )
        page = await context.new_page()

        try:
            # 1. Navigate to the mini app
            opened = await navigate_to_mini_app(page)
            if not opened:
                return False

            # 2. Try to interact with the mini app — look for Search tab
            await page.wait_for_timeout(2000)

            # The mini app might be in an iframe
            frames = page.frames
            target_frame = page  # Default to main page

            for frame in frames:
                try:
                    # Check if this frame has the CinemagicHD content
                    url = frame.url
                    if "cinemagic" in url.lower() or frame != page.main_frame:
                        test = await frame.locator('input[type="search"], .search-input, [placeholder*="Search"]').count()
                        if test > 0:
                            target_frame = frame
                            console.print(f"[green]📍 Found mini app in frame: {url[:60]}[/green]")
                            break
                except Exception:
                    continue

            # 3. Navigate to Search tab
            console.print(f"[cyan]🔍 Searching for: {movie_title}[/cyan]")
            try:
                # Click Search in bottom nav
                search_nav = target_frame.get_by_text("Search", exact=True).first
                await search_nav.click(timeout=5000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

            # Type in search box
            try:
                search_input = target_frame.locator(
                    'input[type="search"], input[placeholder*="Search"], input[placeholder*="Movie"], .search-input'
                ).first
                await search_input.click(timeout=5000)
                await search_input.fill(movie_title)
                await page.wait_for_timeout(2000)
            except Exception as e:
                console.print(f"[red]❌ Could not find search input: {e}[/red]")
                await page.screenshot(path="temp/debug_screenshot.png")
                console.print("[yellow]📸 Debug screenshot saved to temp/debug_screenshot.png[/yellow]")
                return False

            # 4. Click first result
            try:
                first_result = target_frame.locator(
                    '.movie-card, .film-card, .result-item, [class*="movie"], [class*="film"]'
                ).first
                await first_result.click(timeout=5000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                console.print(f"[yellow]⚠️  Could not click result: {e}. Taking screenshot...[/yellow]")
                await page.screenshot(path="temp/debug_search_results.png")
                return False

            # 5. Look for Download button
            console.print("[cyan]⬇️  Looking for download option...[/cyan]")

            # Set up download interception
            download_future = asyncio.Future()

            async def on_download(download):
                console.print(f"[green]📥 Download started: {download.suggested_filename}[/green]")
                await download.save_as(output_path)
                console.print(f"[green]✅ Saved to: {output_path}[/green]")
                if not download_future.done():
                    download_future.set_result(True)

            page.on("download", on_download)
            context.on("page", lambda p: p.on("download", on_download))

            # Try clicking various download-related buttons
            download_selectors = [
                'button:has-text("Download")',
                'a:has-text("Download")',
                '[class*="download"]',
                'button:has-text("720")',
                'button:has-text("1080")',
                '.download-btn',
            ]
            for selector in download_selectors:
                try:
                    btn = target_frame.locator(selector).first
                    count = await btn.count()
                    if count > 0:
                        await btn.click(timeout=3000)
                        console.print(f"[green]✅ Clicked: {selector}[/green]")
                        break
                except Exception:
                    continue

            # Wait for download to complete (up to 10 minutes for large files)
            console.print("[cyan]⏳ Waiting for download... (this can take several minutes)[/cyan]")
            try:
                await asyncio.wait_for(download_future, timeout=600)
                return True
            except asyncio.TimeoutError:
                console.print("[red]❌ Download timed out after 10 minutes[/red]")
                return False

        except Exception as e:
            console.print(f"[red]❌ Download failed: {e}[/red]")
            await page.screenshot(path="temp/debug_error.png")
            return False
        finally:
            await context.close()


def download_movie(movie_title: str, output_path: str) -> bool:
    """Synchronous wrapper for use by main.py."""
    return asyncio.run(search_and_download_movie(movie_title, output_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CinemagicHD Movie Downloader")
    parser.add_argument("--setup", action="store_true", help="First-time Telegram session setup")
    parser.add_argument("--movie", type=str, help="Movie title to download")
    parser.add_argument("--output", type=str, default="temp/movie.mp4", help="Output file path")
    args = parser.parse_args()

    if args.setup:
        asyncio.run(setup_session())
    elif args.movie:
        success = download_movie(args.movie, args.output)
        print("✅ Success" if success else "❌ Failed")
    else:
        parser.print_help()
