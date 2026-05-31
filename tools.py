"""
Tool implementations for the Website Automation Agent.

These are the real-world actions the agent can invoke during its ReAct loop.
Each tool wraps a Playwright browser operation and returns a text observation
that gets fed back to the LLM for the next reasoning step.

Tools:
    take_screenshot   — Capture the current browser viewport as PNG
    open_browser      — Launch a headed Chromium browser instance
    navigate_to_url   — Navigate the browser to a specific URL
    click_on_screen   — Click at (x, y) pixel coordinates
    send_keys         — Type text into the currently focused element
    scroll            — Scroll the page up or down
    double_click      — Double-click at (x, y) pixel coordinates
    done              — Signal that the task is complete
"""

import time
from browser_manager import browser_manager
from dom_parser import parse_dom


# ─── Tool: open_browser ──────────────────────────────────────

def open_browser(**kwargs) -> str:
    """
    Launch a Chromium browser in headed mode.
    Must be called before any other browser tools.
    """
    return browser_manager.launch()


# ─── Tool: navigate_to_url ───────────────────────────────────

def navigate_to_url(url: str) -> str:
    """
    Navigate the browser to a specific URL and wait for the page to load.
    After navigation, extracts the DOM state so the agent knows what's on the page.

    Args:
        url: The URL to navigate to (e.g., "https://example.com")
    """
    page = browser_manager.get_page()
    if not page or page.is_closed():
        return "❌ Browser is not open or page was closed. Call open_browser first."

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        # Navigate and wait for DOM to be ready
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Give JS frameworks a moment to render
        page.wait_for_timeout(2000)

        # Extract DOM state for the agent
        dom_state = parse_dom(page)

        return (
            f"✅ Navigated to: {url}\n"
            f"   Page loaded successfully.\n\n"
            f"{dom_state}"
        )

    except Exception as e:
        return f"❌ Navigation failed: {type(e).__name__}: {e}"


# ─── Tool: take_screenshot ───────────────────────────────────

def take_screenshot(label: str = "screenshot") -> str:
    """
    Capture a screenshot of the current browser viewport.
    Screenshots are saved to the screenshots/ directory for logging.

    Args:
        label: A descriptive label for the screenshot filename.
    """
    page = browser_manager.get_page()
    if not page or page.is_closed():
        return "❌ Browser is not open or page was closed. Call open_browser first."

    result = browser_manager.take_screenshot(label=label)

    # Also return current DOM state so the agent has fresh context
    dom_state = parse_dom(page)
    return f"{result}\n\n{dom_state}"


# ─── Tool: click_on_screen ───────────────────────────────────

def click_on_screen(x: int, y: int) -> str:
    """
    Perform a mouse click at the specified (x, y) pixel coordinates.
    Use coordinates from the interactive elements list provided by DOM parsing.

    Args:
        x: Horizontal pixel coordinate (from left edge)
        y: Vertical pixel coordinate (from top edge)
    """
    page = browser_manager.get_page()
    if not page or page.is_closed():
        return "❌ Browser is not open or page was closed. Call open_browser first."

    try:
        # Ensure coordinates are integers
        x, y = int(x), int(y)

        # Validate viewport bounds
        viewport = page.viewport_size
        if viewport:
            width = viewport["width"]
            height = viewport["height"]
            if x < 0 or y < 0 or x > width or y > height:
                return f"❌ Click failed: Coordinates ({x}, {y}) are outside the viewport bounds ({width}x{height})."

        # Check what element is at coordinates before clicking
        el_info = page.evaluate("""
            ([x, y]) => {
                const el = document.elementFromPoint(x, y);
                if (!el) return null;
                return {
                    tagName: el.tagName,
                    id: el.id,
                    className: el.className,
                    isBackground: el.tagName === 'BODY' || el.tagName === 'HTML'
                };
            }
        """, [x, y])

        # Perform the click
        page.mouse.click(x, y)

        # Wait for any reactions (dropdown opening, focus change, etc.)
        page.wait_for_timeout(500)

        # Get updated DOM state
        dom_state = parse_dom(page)

        if el_info and el_info.get("isBackground"):
            return (
                f"⚠️ Warning: Clicked at ({x}, {y}) but it landed on empty space ({el_info.get('tagName')}). "
                f"No interactive element detected at these coordinates. Please check coordinates and retry.\n\n"
                f"{dom_state}"
            )

        return (
            f"✅ Clicked at ({x}, {y}) (element: <{el_info.get('tagName', 'unknown').lower()}>).\n\n"
            f"{dom_state}"
        )

    except Exception as e:
        return f"❌ Click failed at ({x}, {y}): {type(e).__name__}: {e}"


# ─── Tool: send_keys ─────────────────────────────────────────

def send_keys(text: str) -> str:
    """
    Type text into the currently focused element.
    Should be called after clicking on an input/textarea to focus it.

    The tool first clears any existing content in the field (Ctrl+A → Delete),
    then types the new text character by character for reliability.

    Args:
        text: The text string to type.
    """
    page = browser_manager.get_page()
    if not page or page.is_closed():
        return "❌ Browser is not open or page was closed. Call open_browser first."

    try:
        # Clear any existing text in the focused field
        page.keyboard.press("Control+a")
        page.wait_for_timeout(100)
        page.keyboard.press("Delete")
        page.wait_for_timeout(100)

        # Type the text character by character (more reliable than fill)
        page.keyboard.type(text, delay=30)

        # Small delay for the UI to update
        page.wait_for_timeout(300)

        # Get updated DOM state
        dom_state = parse_dom(page)

        return (
            f"✅ Typed: \"{text}\"\n\n"
            f"{dom_state}"
        )

    except Exception as e:
        return f"❌ send_keys failed: {type(e).__name__}: {e}"


# ─── Tool: scroll ────────────────────────────────────────────

def scroll(direction: str = "down", amount: int = 300) -> str:
    """
    Scroll the page in the specified direction.

    Args:
        direction: "up" or "down" (default: "down")
        amount: Pixels to scroll (default: 300)
    """
    page = browser_manager.get_page()
    if not page or page.is_closed():
        return "❌ Browser is not open or page was closed. Call open_browser first."

    try:
        amount = int(amount)
        delta = amount if direction.lower() == "down" else -amount

        page.mouse.wheel(0, delta)

        # Wait for scroll to settle and any lazy-loaded content
        page.wait_for_timeout(500)

        # Get updated DOM state
        dom_state = parse_dom(page)

        return (
            f"✅ Scrolled {direction} by {amount}px.\n\n"
            f"{dom_state}"
        )

    except Exception as e:
        return f"❌ Scroll failed: {type(e).__name__}: {e}"


# ─── Tool: double_click ──────────────────────────────────────

def double_click(x: int, y: int) -> str:
    """
    Perform a double-click at the specified (x, y) pixel coordinates.
    Useful for selecting text or triggering double-click actions.

    Args:
        x: Horizontal pixel coordinate
        y: Vertical pixel coordinate
    """
    page = browser_manager.get_page()
    if not page or page.is_closed():
        return "❌ Browser is not open or page was closed. Call open_browser first."

    try:
        x, y = int(x), int(y)

        page.mouse.dblclick(x, y)

        # Wait for any reactions
        page.wait_for_timeout(500)

        # Get updated DOM state
        dom_state = parse_dom(page)

        return (
            f"✅ Double-clicked at ({x}, {y}).\n\n"
            f"{dom_state}"
        )

    except Exception as e:
        return f"❌ Double-click failed at ({x}, {y}): {type(e).__name__}: {e}"


# ─── Tool: done ──────────────────────────────────────────────

def done(summary: str) -> str:
    """
    Signal that the automation task is complete.
    Closes the browser and returns the summary.

    Args:
        summary: A description of what was accomplished.
    """
    # Take a final screenshot before closing
    page = browser_manager.get_page()
    if page:
        browser_manager.take_screenshot(label="final_result")

    return f"✅ TASK COMPLETE: {summary}"


# ─── Tool Registry ───────────────────────────────────────────

TOOL_REGISTRY = {
    "open_browser": open_browser,
    "navigate_to_url": navigate_to_url,
    "take_screenshot": take_screenshot,
    "click_on_screen": click_on_screen,
    "send_keys": send_keys,
    "scroll": scroll,
    "double_click": double_click,
    "done": done,
}


def execute_tool(tool_name: str, args: dict) -> str:
    """
    Execute a tool by name with the given arguments.

    Args:
        tool_name: Name of the tool to execute (must be in TOOL_REGISTRY).
        args: Dictionary of arguments to pass to the tool function.

    Returns:
        A string observation from the tool execution.
    """
    if tool_name not in TOOL_REGISTRY:
        available = ", ".join(TOOL_REGISTRY.keys())
        return f"❌ Unknown tool: '{tool_name}'. Available tools: {available}"

    try:
        func = TOOL_REGISTRY[tool_name]
        result = func(**args)
        return result
    except TypeError as e:
        return f"❌ Invalid arguments for {tool_name}: {e}"
    except Exception as e:
        return f"❌ Error executing {tool_name}: {type(e).__name__}: {e}"
