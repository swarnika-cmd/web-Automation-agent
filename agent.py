"""
AutoCli — AI-Powered Browser Automation Agent.

A ReAct-style agent that controls a real web browser autonomously using
Playwright for browser actions and Groq (Llama 3.3 70B) for intelligent
decision-making. The agent reads the page DOM, reasons about what to do,
and executes browser actions in a loop until the task is complete.

Architecture:
    User Input → Agent Loop → LLM (Groq) → Tool Execution (Playwright) → Observation → LLM → ...

Usage:
    python agent.py
    > Navigate to https://example.com and fill the form
"""

import os
import re
import sys
import json
import time
import warnings
import atexit

from typing import Any, Optional
from pathlib import Path
from dotenv import load_dotenv

# ─── Windows terminal fixes ──────────────────────────────────
if sys.platform == "win32":
    os.system("")  # Enable ANSI escape codes on Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Suppress noisy warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

# ─── Imports (after env is loaded) ────────────────────────────
from prompts import SYSTEM_PROMPT, WELCOME_BANNER, HELP_TEXT, DEFAULT_TASK
from tools import execute_tool
from browser_manager import browser_manager

# Rich console for beautiful terminal output
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.rule import Rule
from rich import box

console = Console()


# ─── JSON Extraction ─────────────────────────────────────────

def extract_json_from_response(text: str) -> dict | None:
    """
    Extract the first JSON tool-call object from the LLM's response.

    Tries multiple extraction strategies to handle different response formats:
    1. JSON inside ```json ... ``` code blocks
    2. Raw {"tool": ...} pattern with brace matching
    3. Outermost braces fallback

    Args:
        text: The raw LLM response string.

    Returns:
        A dict with "tool" and "args" keys, or None if no valid call found.
    """
    # Strategy 1: JSON in ```json ... ``` code blocks
    code_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if code_block:
        try:
            candidate = code_block.group(1).strip()
            obj = json.loads(candidate)
            if isinstance(obj, dict) and "tool" in obj:
                return obj
        except json.JSONDecodeError:
            pass

    # Strategy 2: Find {"tool": ...} pattern with brace matching
    tool_match = re.search(r'\{\s*"tool"\s*:', text)
    if tool_match:
        start = tool_match.start()
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    # Strategy 3: Outermost braces fallback
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict) and "tool" in obj:
                        return obj
                except json.JSONDecodeError:
                    start = -1

    return None


# ─── LLM Backend (Groq) ──────────────────────────────────────

class GroqBackend:
    """
    Groq API backend for fast LLM inference.

    Uses Llama 3.3 70B Versatile model via Groq's OpenAI-compatible API.
    This model provides excellent instruction following and JSON output
    at very high inference speeds.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = "llama-3.3-70b-versatile"
        self.url = "https://api.groq.com/openai/v1/chat/completions"
        self.name = f"Groq ({self.model})"

    def chat(self, messages: list) -> str:
        """
        Send a conversation to Groq and get the assistant's response.

        Args:
            messages: List of message dicts with "role" and "content" keys.

        Returns:
            The assistant's response text.

        Raises:
            Exception on API errors (rate limits, auth failures, etc.)
        """
        import httpx

        # Build OpenAI-format messages with system prompt
        openai_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for msg in messages:
            role = msg["role"]
            if role == "model":
                role = "assistant"
            content = msg.get("content", "")
            openai_messages.append({"role": role, "content": content})

        payload = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": 0.1,   # Low temperature for consistent tool calls
            "max_tokens": 4096,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = httpx.post(
            self.url,
            json=payload,
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


def create_backend() -> GroqBackend:
    """
    Create the Groq LLM backend from environment variables.

    Returns:
        A configured GroqBackend instance.

    Exits:
        If no valid GROQ_API_KEY is found in the environment.
    """
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()

    if not groq_key or groq_key.startswith("gsk_your"):
        console.print(Panel(
            "[bold red]No Groq API key found![/bold red]\n\n"
            "Set your API key in the [cyan].env[/cyan] file:\n"
            "  GROQ_API_KEY=gsk_...\n\n"
            "Get a free key at: [link]https://console.groq.com[/link]",
            title="⚠️ Configuration Error",
            border_style="red",
        ))
        sys.exit(1)

    return GroqBackend(groq_key)


def call_llm(backend: GroqBackend, messages: list) -> str:
    """
    Call the LLM with retry logic for rate limits.

    Args:
        backend: The GroqBackend instance.
        messages: Conversation history.

    Returns:
        The LLM's response text.

    Raises:
        RuntimeError if all retries are exhausted.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return backend.chat(messages)
        except Exception as e:
            error_str = str(e)

            # Rate limit handling
            if "429" in error_str or "rate" in error_str.lower():
                wait_match = re.search(r'(\d+)', error_str)
                wait_secs = min(int(wait_match.group(1)), 60) if wait_match else 15
                console.print(f"  [yellow]⏳ Rate limited. Waiting {wait_secs}s... (attempt {attempt + 1}/{max_retries})[/yellow]")
                time.sleep(wait_secs)
                continue

            if attempt == max_retries - 1:
                raise RuntimeError(f"LLM call failed after {max_retries} attempts: {error_str[:200]}")

            console.print(f"  [yellow]⚠ LLM error: {error_str[:100]}. Retrying...[/yellow]")
            time.sleep(2)

    raise RuntimeError("LLM call failed: all retries exhausted")


# ─── LLM Backend (Gemini Vision) ─────────────────────────────

class GeminiBackend:
    """
    Gemini API backend for vision-based calls.
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")
        self.name = "Gemini (gemini-1.5-flash)"

    def chat_with_vision(self, messages: list, screenshot_path: str) -> str:
        """
        Send conversation history and the latest screenshot to Gemini.
        """
        from PIL import Image
        parts = []
        
        # Add system instructions
        parts.append(f"SYSTEM INSTRUCTIONS:\n{SYSTEM_PROMPT}\n\n")
        
        # Add conversation history
        parts.append("CONVERSATION HISTORY:\n")
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            parts.append(f"{role.upper()}: {content}\n")
            
        # Add the image part
        img_path = Path(__file__).parent / screenshot_path
        if img_path.exists():
            try:
                img = Image.open(img_path)
                parts.append(img)
                parts.append("\n[Above is the live screenshot of the current page view. Use it to confirm page state, coordinates, and visually verify your actions before planning the next tool call.]")
            except Exception as e:
                parts.append(f"\n[Warning: Failed to load screenshot file: {e}]")
        else:
            parts.append(f"\n[Warning: Screenshot file not found at {screenshot_path}]")
            
        parts.append("\n\nBased on the system instructions, conversation history, and current screenshot above, what is your next action? Output a short thinking line followed by exactly one JSON tool call.")
        
        response = self.model.generate_content(parts)
        return response.text


def call_gemini(backend: GeminiBackend, messages: list, screenshot_path: str) -> str:
    """Call the Gemini vision backend with retries."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return backend.chat_with_vision(messages, screenshot_path)
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate" in error_str.lower() or "quota" in error_str.lower():
                wait_secs = 15
                console.print(f"  [yellow]⏳ Gemini rate limited. Waiting {wait_secs}s... (attempt {attempt + 1}/{max_retries})[/yellow]")
                time.sleep(wait_secs)
                continue
            if attempt == max_retries - 1:
                raise RuntimeError(f"Gemini vision call failed after {max_retries} attempts: {error_str[:200]}")
            console.print(f"  [yellow]⚠ Gemini error: {error_str[:100]}. Retrying...[/yellow]")
            time.sleep(2)
    raise RuntimeError("Gemini vision call failed: all retries exhausted")


# ─── Conversation History Management ─────────────────────────

MAX_HISTORY_CHARS = 80_000  # Max total chars before pruning kicks in

def trim_history(chat_history: list) -> list:
    """
    Prune conversation history to prevent 413 payload-too-large errors.

    Strategy: Keep the most recent 4 messages in full.
    For older messages, compress tool observations to just their
    first line (e.g. '✅ Clicked at (450, 320)') — the DOM state
    details from old steps are stale anyway.

    This keeps the total payload well under Groq's limit.
    """
    total = sum(len(m.get("content", "")) for m in chat_history)
    if total <= MAX_HISTORY_CHARS:
        return chat_history  # No pruning needed

    # Keep last 4 messages untouched (current step context)
    keep_full = 4
    if len(chat_history) <= keep_full:
        return chat_history

    pruned = []
    for i, msg in enumerate(chat_history):
        if i >= len(chat_history) - keep_full:
            # Keep recent messages in full
            pruned.append(msg)
        elif msg["role"] == "user" and msg["content"].startswith("[Tool Result"):
            # Compress old tool results to first line only
            first_line = msg["content"].split("\n")[0]
            pruned.append({"role": "user", "content": first_line + "\n(older observation pruned)"})
        elif msg["role"] == "model" and len(msg.get("content", "")) > 500:
            # Compress old LLM responses to first 200 chars
            pruned.append({"role": "model", "content": msg["content"][:200] + "..."})
        else:
            pruned.append(msg)

    return pruned


# ─── Agent Loop ──────────────────────────────────────────────

def run_agent_loop(user_message: str, chat_history: list, backend: GroqBackend, gemini_backend: Optional[Any] = None) -> tuple[list, Any]:
    """
    Run the ReAct agent loop for a single user instruction.

    The agent iterates: think → act → observe → think → act → ...
    until it calls the 'done' tool or reaches the maximum step limit.

    Each step:
    1. Sends the conversation history to the LLM
    2. LLM responds with thinking + a JSON tool call
    3. Tool is executed via Playwright
    4. Observation (including DOM state) is appended to history
    5. Loop continues

    Args:
        user_message: The user's instruction.
        chat_history: The ongoing conversation history (mutated in place).
        backend: The LLM backend to use.
        gemini_backend: Optional Gemini vision backend to run when screenshots are present.

    Returns:
        A tuple of (updated chat_history, EventLog).
    """
    from event_log import EventLog
    from report_generator import generate_report
    import dashboard
    from typing import Optional

    MAX_STEPS = 20
    MAX_RETRIES = 3
    retry_count = 0

    # Initialize event log
    event_log = EventLog(task_description=user_message)
    dashboard.current_event_log = event_log

    # Log initial event
    event_log.add(step=0, event_type="thinking", message=f"Received task: {user_message}")
    dashboard.broadcast_event({
        "type": "init",
        "task_description": event_log.task_description,
        "status": event_log.status,
        "start_time": event_log.start_time,
        "events": [e.to_dict() for e in event_log.events]
    })

    # Add user message to conversation
    chat_history.append({
        "role": "user",
        "content": user_message,
    })

    for step in range(1, MAX_STEPS + 1):
        # ─── Step header ──────────────────────────────────
        console.print()
        console.print(Rule(f"[bold cyan]Step {step}/{MAX_STEPS}[/bold cyan]", style="dim"))

        # Log thinking status to event_log
        event_log.add(step=step, event_type="thinking", message="Analyzing page state and planning next action...")
        dashboard.broadcast_event({"type": "event", "event": event_log.events[-1].to_dict()})

        # ─── Call the LLM (vision-enabled if screenshot exists and gemini is active) ───
        try:
            # Prune history before sending to prevent 413 errors
            trimmed = trim_history(chat_history)
            
            # Find if there is a recent screenshot in the event log
            latest_screenshot = None
            for event in reversed(event_log.events):
                if event.screenshot_path:
                    latest_screenshot = event.screenshot_path
                    break
            
            with console.status("[bold blue]Agent is thinking...[/bold blue]", spinner="dots"):
                if latest_screenshot and gemini_backend:
                    console.print(f"  [dim]Using Gemini Vision for screenshot analysis...[/dim]")
                    response_text = call_gemini(gemini_backend, trimmed, latest_screenshot)
                else:
                    response_text = call_llm(backend, trimmed)
        except RuntimeError as e:
            console.print(Panel(
                f"[bold red]{e}[/bold red]\n\n"
                "[yellow]Options:[/yellow]\n"
                "  1. Wait for rate limit to reset\n"
                "  2. Get a new API key from https://console.groq.com",
                title="LLM Error",
                border_style="red",
            ))
            event_log.add(step=step, event_type="error", message=f"LLM call failed: {e}")
            dashboard.broadcast_event({"type": "event", "event": event_log.events[-1].to_dict()})
            break

        # ─── Extract thinking (text before JSON) ──────────
        thinking = ""
        thinking_match = re.search(
            r'(?:Thinking:|💭)(.*?)(?:```|\{)',
            response_text, re.DOTALL | re.IGNORECASE
        )
        if thinking_match:
            thinking = thinking_match.group(1).strip()
        else:
            # Show non-JSON text as thinking
            non_json = re.sub(r'```[\s\S]*?```', '', response_text).strip()
            non_json = re.sub(r'\{[\s\S]*\}', '', non_json).strip()
            if non_json and len(non_json) > 5:
                thinking = non_json[:300]

        if thinking:
            console.print(f"  [dim italic]💭 {thinking}[/dim italic]")
            # Log actual thinking message
            event_log.add(step=step, event_type="thinking", message=thinking)
            dashboard.broadcast_event({"type": "event", "event": event_log.events[-1].to_dict()})

        # ─── Extract tool call from LLM response ─────────
        tool_call = extract_json_from_response(response_text)

        if not tool_call or "tool" not in tool_call:
            console.print("  [yellow]⚠ No valid tool call found. Nudging agent...[/yellow]")
            chat_history.append({"role": "model", "content": response_text})
            chat_history.append({
                "role": "user",
                "content": (
                    "Please respond with a valid JSON tool call. "
                    'Format: {"tool": "tool_name", "args": {...}}\n'
                    "Available tools: open_browser, navigate_to_url, take_screenshot, "
                    "click_on_screen, send_keys, scroll, double_click, done"
                ),
            })
            event_log.add(step=step, event_type="error", message="Invalid tool call format received from LLM.")
            dashboard.broadcast_event({"type": "event", "event": event_log.events[-1].to_dict()})
            continue

        tool_name = tool_call["tool"]
        tool_args = tool_call.get("args", {})

        # ─── Display the action ──────────────────────────
        if tool_name == "send_keys":
            args_display = f'text="{tool_args.get("text", "?")}"'
        elif tool_name == "navigate_to_url":
            args_display = f'url="{tool_args.get("url", "?")}"'
        elif tool_name in ("click_on_screen", "double_click"):
            args_display = f'x={tool_args.get("x", "?")}, y={tool_args.get("y", "?")}'
        elif tool_name == "scroll":
            args_display = f'{tool_args.get("direction", "down")}, {tool_args.get("amount", 300)}px'
        elif tool_name == "take_screenshot":
            args_display = f'label="{tool_args.get("label", "screenshot")}"'
        elif tool_name == "done":
            args_display = f'summary="{tool_args.get("summary", "")[:80]}"'
        else:
            args_display = ", ".join(f'{k}="{str(v)[:50]}"' for k, v in tool_args.items())

        console.print(f"  [bold yellow]🔧 {tool_name}[/bold yellow]({args_display})")

        # Log tool call to event_log
        event_log.add(
            step=step,
            event_type="tool_call",
            message=f"Invoking {tool_name}",
            tool_name=tool_name,
            tool_args=tool_args
        )
        dashboard.broadcast_event({"type": "event", "event": event_log.events[-1].to_dict()})

        # ─── Execute the tool ─────────────────────────────
        start_time = time.time()
        with console.status(f"[blue]Executing {tool_name}...[/blue]", spinner="dots"):
            observation = execute_tool(tool_name, tool_args)
        duration_ms = int((time.time() - start_time) * 1000)

        # ─── Display observation (truncated for readability) ──
        obs_lines = observation.strip().split("\n")
        if len(obs_lines) <= 8:
            for line in obs_lines:
                console.print(f"  [green]{line}[/green]")
        else:
            for line in obs_lines[:6]:
                console.print(f"  [green]{line}[/green]")
            console.print(f"  [dim]... ({len(obs_lines) - 6} more lines)[/dim]")

        # Extract screenshot path if saved
        screenshot_path = None
        if "Screenshot saved: " in observation:
            match = re.search(r'Screenshot saved: (screenshots/[^\s\n\)]+)', observation)
            if match:
                screenshot_path = match.group(1)

        # ─── Update conversation history ──────────────────
        chat_history.append({"role": "model", "content": response_text})

        # ─── Check for Failure & Self-Healing Retry ────────
        is_failure = "❌" in observation or "failed" in observation.lower() or "warning" in observation.lower() or "empty space" in observation.lower()
        if is_failure:
            retry_count += 1
            if retry_count <= MAX_RETRIES:
                console.print(f"  [bold orange3]⚠️ Self-Healing: Action failed. Retrying ({retry_count}/{MAX_RETRIES})...[/bold orange3]")
                
                # Take a fresh screenshot for healing context
                screenshot_res = browser_manager.take_screenshot(label=f"retry_{retry_count}")
                retry_screenshot_path = None
                if "Screenshot saved: " in screenshot_res:
                    match = re.search(r'Screenshot saved: (screenshots/[^\s\n\)]+)', screenshot_res)
                    if match:
                        retry_screenshot_path = match.group(1)

                # Log retry event
                event_log.add(
                    step=step,
                    event_type="retry",
                    message=f"Self-Healing: Previous action failed. Retrying ({retry_count}/{MAX_RETRIES}).\nError: {observation[:200]}",
                    tool_name=tool_name,
                    tool_args=tool_args,
                    screenshot_path=retry_screenshot_path,
                    duration_ms=duration_ms
                )
                dashboard.broadcast_event({"type": "event", "event": event_log.events[-1].to_dict()})

                # Inject retry prompt to LLM
                chat_history.append({
                    "role": "user",
                    "content": (
                        f"⚠️ SELF-HEALING: The previous action failed.\n"
                        f"Error: {observation[:1000]}\n\n"
                        f"{screenshot_res}\n\n"
                        f"Retry attempt {retry_count}/{MAX_RETRIES}.\n"
                        f"Please analyze the current page state, correct coordinates/inputs, and try a different approach.\n"
                        f"For example, scroll to bring elements into viewport, wait, or click alternative coordinates."
                    ),
                })
                continue
            else:
                console.print("  [bold red]❌ Self-Healing: Max retries exceeded. Proceeding anyway.[/bold red]")

        # Reset retry counter on success
        retry_count = 0

        # Log observation success
        obs_summary = observation.split("\n")[0] if "Screenshot saved" in observation else observation[:200]
        event_log.add(
            step=step,
            event_type="observation",
            message=f"Observation: {obs_summary}",
            tool_name=tool_name,
            tool_args=tool_args,
            screenshot_path=screenshot_path,
            duration_ms=duration_ms
        )
        dashboard.broadcast_event({"type": "event", "event": event_log.events[-1].to_dict()})

        # Truncate observations aggressively for LLM context window
        obs_for_llm = observation[:6_000]
        if len(observation) > 6_000:
            obs_for_llm += "\n... (truncated)"

        chat_history.append({
            "role": "user",
            "content": (
                f"[Tool Result for {tool_name}]:\n{obs_for_llm}\n\n"
                "Continue to the next step. Use a tool to proceed."
            ),
        })

        # ─── Check if task is done ────────────────────────
        if tool_name == "done":
            event_log.finish("success")
            dashboard.broadcast_event({"type": "status", "status": "success"})
            console.print()
            console.print(Panel(
                f"[bold green]✅ Task Complete![/bold green]\n\n"
                f"{tool_args.get('summary', '')}",
                border_style="green",
                box=box.DOUBLE,
            ))
            break

    else:
        # Reached max steps without completing
        event_log.finish("failed")
        dashboard.broadcast_event({"type": "status", "status": "failed"})
        console.print()
        console.print(Panel(
            f"[yellow]Reached max steps ({MAX_STEPS}).[/yellow]\n"
            "Type [bold]continue[/bold] to keep going, or give new instructions.",
            border_style="yellow",
        ))

    # Generate HTML Run Report
    try:
        report_path = generate_report(event_log)
        console.print(f"📄 HTML Run Report saved: [cyan]{report_path}[/cyan]")
    except Exception as e:
        console.print(f"[bold red]⚠️ Failed to generate run report: {e}[/bold red]")

    return chat_history, event_log


# ─── Cleanup on exit ─────────────────────────────────────────

def cleanup():
    """Close the browser when the program exits."""
    if browser_manager.is_open():
        browser_manager.close()

atexit.register(cleanup)


# ─── Main CLI ────────────────────────────────────────────────

def main():
    """
    Main entry point — interactive CLI for the automation agent.

    Supports commands:
        fill     — Run the default form-filling task
        reset    — Clear conversation history
        help     — Show help
        exit/q   — Quit
        <text>   — Any natural language instruction
    """
    from typing import Any
    # Initialize LLM backend
    backend = create_backend()
    chat_history = []

    # Initialize optional Gemini backend for vision tasks
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    gemini_backend = None
    if gemini_key and not gemini_key.startswith("your_"):
        try:
            gemini_backend = GeminiBackend(gemini_key)
        except Exception as e:
            console.print(f"  [yellow]⚠️ Failed to initialize Gemini backend: {e}[/yellow]")

    # Start live dashboard background thread
    from dashboard import start_dashboard
    start_dashboard()

    # Welcome screen
    console.print(WELCOME_BANNER)
    console.print(f"  [dim]LLM Backend: {backend.name}[/dim]")
    if gemini_backend:
        console.print(f"  [dim]Vision Backend: {gemini_backend.name}[/dim]")
    else:
        console.print(f"  [dim]Vision Backend: Disabled (No GEMINI_API_KEY found)[/dim]")
    console.print(f"  [dim]Screenshots: ./screenshots/[/dim]")
    console.print(f"  [bold green]🖥️  Live Dashboard: http://localhost:8765[/bold green]")
    console.print()

    # Main input loop
    while True:
        try:
            user_input = console.input("[bold white]You > [/bold white]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n\n[cyan]👋 Goodbye![/cyan]")
            break

        if not user_input:
            continue

        cmd = user_input.lower().strip()

        # ─── Built-in commands ────────────────────────────
        if cmd in ("exit", "quit", "q"):
            console.print("\n[cyan]👋 Goodbye![/cyan]")
            break

        if cmd == "help":
            console.print(HELP_TEXT)
            continue

        if cmd == "reset":
            chat_history = []
            # Close browser if open
            if browser_manager.is_open():
                browser_manager.close()
            console.print("[yellow]🔄 Conversation reset. Browser closed. Start fresh![/yellow]\n")
            continue

        # ─── Quick command: fill the target form ──────────
        if cmd == "fill":
            user_input = DEFAULT_TASK
            console.print("\n[bold magenta]🚀 Starting default form-filling task...[/bold magenta]")
            console.print(Rule(style="dim"))

        # ─── Run the agent loop ───────────────────────────
        chat_history, event_log = run_agent_loop(user_input, chat_history, backend, gemini_backend)


if __name__ == "__main__":
    # Ensure Typing is imported globally or inside function
    from typing import Any
    main()
