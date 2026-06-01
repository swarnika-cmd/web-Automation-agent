"""
Browser Manager — Playwright browser lifecycle management.

Provides a singleton-style manager for launching, accessing, and closing
a Chromium browser instance. Uses headed mode so the automation is visible
during live demos (viva presentations).

Usage:
    manager = BrowserManager()
    manager.launch()
    page = manager.get_page()
    # ... do stuff with page ...
    manager.close()
"""

import io
import os
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext


# Directory for saving screenshots and videos
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
VIDEO_DIR = Path(__file__).parent / "videos"


class BrowserManager:
    """
    Manages a single Playwright Chromium browser instance.

    Design decisions:
    - Headed mode (headless=False) for live demo visibility
    - Single tab/page model — the agent works on one page at a time
    - Viewport set to 1280x720 for consistent screenshot sizes
    - Slow-mo of 100ms so actions are visible during demos
    """

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._screenshot_counter = 0
        self._latest_screenshot_bytes: bytes | None = None

        # Ensure directories exist
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    def launch(self) -> str:
        """
        Launch a Chromium browser.
        Returns a status message.
        """
        if self._browser and self._browser.is_connected():
            return "⚠️ Browser is already open."

        try:
            headless = os.environ.get("HEADLESS", "False").lower() == "true"
            slow_mo_val = 0 if headless else 100
            
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                slow_mo=slow_mo_val,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                record_video_dir=str(VIDEO_DIR),
                record_video_size={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            self._page = self._context.new_page()
            self._screenshot_counter = 0
            self._latest_screenshot_bytes = None
            mode_str = "headless" if headless else "headed"
            return f"✅ Browser launched successfully (Chromium, {mode_str} mode, 1280x720, video enabled)."

        except Exception as e:
            return f"❌ Failed to launch browser: {type(e).__name__}: {e}"

    def get_page(self) -> Page | None:
        """Get the active browser page, or None if browser isn't open."""
        return self._page

    def is_open(self) -> bool:
        """Check if the browser is currently open and connected."""
        return self._browser is not None and self._browser.is_connected()

    def take_screenshot(self, label: str = "screenshot") -> str:
        """
        Capture a screenshot of the current page.

        The screenshot is compressed to reduce file size:
        - Resized to 800x600 (from 1280x720 viewport)
        - Saved as JPEG at 25% quality
        This brings ~2MB PNGs down to ~20-30KB, preventing
        payload size issues when the conversation grows large.

        Args:
            label: A descriptive label used in the filename.

        Returns:
            The file path of the saved screenshot, or an error message.
        """
        if not self._page or self._page.is_closed():
            return "❌ No browser page available or it has been closed."

        self._screenshot_counter += 1
        # Sanitize label for filename
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        filename = f"step_{self._screenshot_counter:03d}_{safe_label}.jpg"
        filepath = SCREENSHOT_DIR / filename

        try:
            # Capture as raw PNG bytes in memory
            raw_bytes = self._page.screenshot(full_page=False)

            # Resize + compress with Pillow → JPEG 25% quality
            img = Image.open(io.BytesIO(raw_bytes))
            img = img.resize((800, 600), Image.LANCZOS)
            img = img.convert("RGB")  # JPEG doesn't support alpha
            
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=25, optimize=True)
            self._latest_screenshot_bytes = buffer.getvalue()
            
            # Save bytes to file
            filepath.write_bytes(self._latest_screenshot_bytes)

            size_kb = filepath.stat().st_size / 1024
            return (
                f"✅ Screenshot saved: screenshots/{filename} ({size_kb:.1f} KB)\n"
                f"   Resolution: 800x600 JPEG | Page title: {self._page.title()}"
            )
        except Exception as e:
            return f"❌ Screenshot failed: {type(e).__name__}: {e}"

    def get_latest_screenshot_bytes(self) -> bytes | None:
        """
        Returns the latest cached screenshot bytes, or captures a new one
        if the page is open but no screenshot has been saved yet.
        """
        if self._latest_screenshot_bytes:
            return self._latest_screenshot_bytes

        if self._page:
            try:
                raw_bytes = self._page.screenshot(full_page=False)
                img = Image.open(io.BytesIO(raw_bytes))
                img = img.resize((800, 600), Image.LANCZOS)
                img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=25, optimize=True)
                self._latest_screenshot_bytes = buffer.getvalue()
                return self._latest_screenshot_bytes
            except Exception:
                pass
        return None

    def close(self) -> str:
        """Close the browser and clean up Playwright resources."""
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

        return "✅ Browser closed."


# ─── Global singleton instance ────────────────────────────────
browser_manager = BrowserManager()
