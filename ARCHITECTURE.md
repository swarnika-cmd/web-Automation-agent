# Architecture Document — AutoCli Browser Automation Agent

This document explains the key design decisions, agent workflow, and technical trade-offs made in building the AutoCli browser automation agent.

---

## 1. Agent Architecture: ReAct Loop

**Decision:** Use a ReAct (Reasoning + Acting) loop pattern.

**Why:** The ReAct pattern is the standard approach for tool-using LLM agents. The agent alternates between:
- **Thinking** — The LLM reasons about the current page state and decides what to do next
- **Acting** — A tool is executed (click, type, scroll, etc.)
- **Observing** — The tool returns the result + updated DOM state

This creates a natural feedback loop where each action updates the agent's understanding of the page, allowing it to make intelligent sequential decisions.

**Alternative considered:** Plan-then-execute (generate all actions upfront). Rejected because web pages are dynamic — the DOM changes after each interaction, so the agent needs to re-assess after every step.

---

## 2. Page Understanding: DOM-Based vs Vision-Based

**Decision:** Extract the DOM structure as text instead of sending screenshots to a vision model.

**Why:**
- **Model availability** — Groq's Llama 3.3 70B does not support image inputs. DOM-based approach works with any text LLM.
- **Reliability** — DOM extraction gives exact element coordinates, types, labels, and values. Vision models can misidentify elements or coordinates.
- **Speed** — Text processing is faster than image analysis. Groq provides ~200 tokens/sec inference.
- **Cost** — Text-only API calls are cheaper than vision API calls.

**How it works:**
1. After each browser action, we inject JavaScript into the page via `page.evaluate()`
2. The script enumerates all visible interactive elements (inputs, buttons, links, etc.)
3. For each element, it captures: tag, type, label, placeholder, value, coordinates, visibility
4. This is formatted as a numbered list and sent to the LLM

**Trade-off:** The agent can't see visual layout, images, or non-interactive content. But for form-filling tasks, the interactive element list provides everything needed.

---

## 3. Tool Design: One Tool Per Action

**Decision:** Each tool performs exactly one atomic browser action.

**Why:** This gives the LLM fine-grained control and makes the system debuggable. The 7 tools map directly to fundamental browser interactions:

| Tool | Playwright API | Purpose |
|------|---------------|---------|
| `open_browser` | `chromium.launch()` | Start the browser |
| `navigate_to_url` | `page.goto()` | Load a page |
| `take_screenshot` | `page.screenshot()` | Capture state |
| `click_on_screen` | `page.mouse.click()` | Click at coordinates |
| `send_keys` | `page.keyboard.type()` | Type text |
| `scroll` | `page.mouse.wheel()` | Scroll the page |
| `double_click` | `page.mouse.dblclick()` | Double-click |

**Key design choice:** Every tool (except `open_browser` and `done`) automatically returns the updated DOM state after execution. This means the LLM always has fresh context without needing to explicitly call `take_screenshot` for page state (though it still can for logging).

---

## 4. LLM Integration: Groq with Llama 3.3 70B

**Decision:** Use Groq's API with Llama 3.3 70B Versatile.

**Why:**
- **Speed** — Groq provides the fastest inference (~200 tokens/sec), critical for responsive automation
- **Quality** — Llama 3.3 70B handles instruction following and JSON output reliably
- **Free tier** — Groq offers a generous free tier for development
- **OpenAI-compatible API** — Simple HTTP calls, no SDK dependency

**JSON extraction:** The LLM's response is parsed with 3 fallback strategies (code blocks → brace matching → outermost braces) because LLMs don't always produce perfectly formatted JSON.

---

## 5. Browser Configuration

**Decision:** Headed mode with slow_mo=100ms.

**Why:**
- **Demo visibility** — The browser is visible during viva presentations
- **Slow-mo** — 100ms delay between actions makes the automation visually trackable
- **Viewport** — Fixed at 1280x720 for consistent screenshot sizes
- **User-Agent** — Set to a real Chrome user-agent to avoid bot detection

---

## 6. Error Recovery Strategy

The agent handles errors at multiple levels:

1. **LLM level** — Rate limit retry with exponential backoff, max 3 attempts
2. **Tool level** — Each tool catches exceptions and returns descriptive error messages
3. **Agent level** — If the LLM doesn't produce a valid tool call, it gets "nudged" with available tools
4. **Max steps** — The loop caps at 20 steps to prevent infinite loops
5. **Cleanup** — `atexit` handler ensures the browser closes even on crashes

---

## 7. Agent Workflow for Form Filling

```
Step 1:  open_browser          → Launch Chromium
Step 2:  navigate_to_url       → Load the target page
Step 3:  take_screenshot       → Log initial state
Step 4:  scroll (if needed)    → Find the form section
Step 5:  click_on_screen       → Focus the Name input
Step 6:  send_keys             → Type the name
Step 7:  take_screenshot       → Verify Name was filled
Step 8:  click_on_screen       → Focus the Description textarea
Step 9:  send_keys             → Type the description
Step 10: take_screenshot       → Verify Description was filled
Step 11: click_on_screen       → Click Submit button
Step 12: take_screenshot       → Capture result
Step 13: done                  → Report completion
```

The LLM decides the exact sequence based on what it sees in the DOM. The workflow above is the expected happy path, but the agent adapts if elements aren't immediately visible or if it encounters unexpected UI patterns.

---

## 8. Comparison with Browser-Use

This project is a simplified version of [browser-use](https://github.com/browser-use/browser-use):

| Feature | Browser-Use | AutoCli |
|---------|------------|---------|
| Browser engine | Playwright | Playwright |
| LLM integration | Multiple (OpenAI, etc.) | Groq (Llama 3.3) |
| Page understanding | Vision + DOM | DOM only |
| Multi-tab | Yes | Single tab |
| Complex workflows | Yes (chains) | Single task loop |
| Error recovery | Advanced | Basic retry |
| Tool count | 15+ | 8 |

AutoCli focuses on simplicity and demonstrating the core concept: an LLM reading page state and deciding browser actions in a loop.
