"""
DOM Parser — Extracts interactive elements from the live browser page.

This is the agent's "eyes". Instead of sending screenshots to a vision model,
we extract a structured list of all interactive elements from the page DOM.
The LLM reads this list to decide what to click, type, or scroll to.

The extraction runs JavaScript inside the browser page via Playwright's
`page.evaluate()` to enumerate visible, interactive elements with their:
- Tag name and type (INPUT, BUTTON, TEXTAREA, SELECT, A)
- Text content / placeholder / label
- Bounding box coordinates (x, y, width, height)
- Attributes (name, id, aria-label, role)
- Current value (for pre-filled inputs)

This approach works with any LLM (no vision capability needed) and is
more reliable than coordinate-based visual recognition.
"""


# JavaScript to run inside the browser page
# Extracts all interactive elements with their properties and positions
EXTRACT_ELEMENTS_JS = """
() => {
    const results = [];
    
    // Selectors for interactive elements
    const selectors = [
        'input:not([type="hidden"])',
        'textarea',
        'button',
        'select',
        'a[href]',
        '[role="button"]',
        '[role="link"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[contenteditable="true"]',
    ];
    
    const allElements = document.querySelectorAll(selectors.join(', '));
    
    for (const el of allElements) {
        const rect = el.getBoundingClientRect();
        
        // Skip elements that are not visible or have zero size
        if (rect.width === 0 || rect.height === 0) continue;
        if (rect.top > window.innerHeight + 500) continue;  // Way below viewport
        
        // Check computed visibility
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        
        // Find associated label
        let label = '';
        if (el.id) {
            const labelEl = document.querySelector(`label[for="${el.id}"]`);
            if (labelEl) label = labelEl.textContent.trim();
        }
        if (!label) {
            // Check parent label
            const parentLabel = el.closest('label');
            if (parentLabel) label = parentLabel.textContent.trim();
        }
        if (!label) {
            // Check aria-label
            label = el.getAttribute('aria-label') || '';
        }
        if (!label) {
            // Check aria-labelledby
            const labelledBy = el.getAttribute('aria-labelledby');
            if (labelledBy) {
                const refEl = document.getElementById(labelledBy);
                if (refEl) label = refEl.textContent.trim();
            }
        }
        
        // Get text content for buttons and links
        let text = '';
        if (['BUTTON', 'A'].includes(el.tagName) || el.getAttribute('role') === 'button') {
            text = el.textContent.trim().substring(0, 80);
        }
        
        // Build element info object
        const info = {
            tag: el.tagName,
            type: el.getAttribute('type') || '',
            name: el.getAttribute('name') || '',
            id: el.id || '',
            placeholder: el.getAttribute('placeholder') || '',
            label: label.substring(0, 80),
            text: text,
            value: el.value || '',
            role: el.getAttribute('role') || '',
            href: el.tagName === 'A' ? (el.getAttribute('href') || '').substring(0, 100) : '',
            disabled: el.disabled || false,
            readonly: el.readOnly || false,
            checked: el.checked || false,
            // Center coordinates (what the agent clicks on)
            x: Math.round(rect.left + rect.width / 2),
            y: Math.round(rect.top + rect.height / 2),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            // Is it in the visible viewport?
            inViewport: (
                rect.top >= 0 &&
                rect.left >= 0 &&
                rect.bottom <= window.innerHeight &&
                rect.right <= window.innerWidth
            ),
        };
        
        results.push(info);
    }
    
    return {
        elements: results,
        page: {
            title: document.title,
            url: window.location.href,
            scrollY: window.scrollY,
            scrollHeight: document.documentElement.scrollHeight,
            viewportHeight: window.innerHeight,
            viewportWidth: window.innerWidth,
        }
    };
}
"""


# JavaScript to extract visible text content (headings, paragraphs, labels)
# Gives the LLM context about what's on screen
EXTRACT_PAGE_CONTEXT_JS = """
() => {
    const context = [];
    
    // Get all headings
    const headings = document.querySelectorAll('h1, h2, h3, h4');
    for (const h of headings) {
        const rect = h.getBoundingClientRect();
        if (rect.height > 0 && rect.top < window.innerHeight + 200) {
            context.push({
                type: 'heading',
                tag: h.tagName,
                text: h.textContent.trim().substring(0, 120),
                y: Math.round(rect.top + window.scrollY),
            });
        }
    }
    
    // Get form labels
    const labels = document.querySelectorAll('label');
    for (const l of labels) {
        const rect = l.getBoundingClientRect();
        if (rect.height > 0 && rect.top < window.innerHeight + 200) {
            context.push({
                type: 'label',
                for: l.getAttribute('for') || '',
                text: l.textContent.trim().substring(0, 80),
                y: Math.round(rect.top + window.scrollY),
            });
        }
    }
    
    return context;
}
"""


def parse_dom(page) -> str:
    """
    Extract interactive elements and page context from the live browser page.

    Args:
        page: Playwright Page object.

    Returns:
        A formatted string listing all interactive elements with coordinates,
        ready to be sent to the LLM for decision-making.
    """
    try:
        # Extract interactive elements
        data = page.evaluate(EXTRACT_ELEMENTS_JS)
        elements = data["elements"]
        page_info = data["page"]

        # Extract page context (headings, labels)
        context = page.evaluate(EXTRACT_PAGE_CONTEXT_JS)

    except Exception as e:
        return f"❌ DOM extraction failed: {type(e).__name__}: {e}"

    # ─── Build the formatted report ──────────────────────────
    MAX_ELEMENTS = 30   # Cap to prevent payload bloat
    MAX_CONTEXT = 6     # Headings/labels to show

    lines = []
    lines.append("🌐 PAGE STATE")
    lines.append(f"   URL: {page_info['url']}")
    lines.append(f"   Title: {page_info['title']}")
    lines.append(f"   Scroll: {page_info['scrollY']}px / {page_info['scrollHeight']}px")
    lines.append("")

    # Page context (headings visible) — capped
    if context:
        lines.append("📄 VISIBLE CONTENT:")
        for item in context[:MAX_CONTEXT]:
            if item["type"] == "heading":
                lines.append(f"   {item['tag']}: {item['text']}")
            elif item["type"] == "label":
                lines.append(f"   Label: {item['text']}")
        lines.append("")

    # Interactive elements
    if not elements:
        lines.append("⚠️ No interactive elements found on this page.")
        return "\n".join(lines)

    # ─── Prioritize: form inputs > buttons > links ────────
    # This ensures the LLM always sees the important form elements
    # even if the page has 200+ links in the nav/footer.
    form_els = [e for e in elements if e["tag"] in ("INPUT", "TEXTAREA", "SELECT")]
    button_els = [e for e in elements if e["tag"] == "BUTTON" or e.get("role") == "button"]
    other_els = [e for e in elements if e not in form_els and e not in button_els]

    prioritized = form_els + button_els + other_els
    shown = prioritized[:MAX_ELEMENTS]
    total = len(elements)

    lines.append(f"🎯 INTERACTIVE ELEMENTS (showing {len(shown)}/{total}):")
    lines.append("")

    for i, el in enumerate(shown, 1):
        tag = el["tag"]
        el_type = el.get("type", "")
        viewport_marker = "👁️" if el["inViewport"] else "📍"

        # Build a compact descriptive line
        parts = [f"[{i}] {viewport_marker} {tag}"]

        if el_type:
            parts[0] += f' type="{el_type}"'

        # Add identifying info (most useful first)
        if el["label"]:
            parts.append(f'label="{el["label"]}"')
        elif el["placeholder"]:
            parts.append(f'placeholder="{el["placeholder"]}"')
        elif el["text"]:
            parts.append(f'text="{el["text"][:50]}"')
        elif el["name"]:
            parts.append(f'name="{el["name"]}"')
        elif el["id"]:
            parts.append(f'id="{el["id"]}"')

        # Show current value for inputs
        if el["value"] and tag in ("INPUT", "TEXTAREA", "SELECT"):
            parts.append(f'value="{el["value"][:30]}"')

        # Show state flags
        if el["disabled"]:
            parts.append("[DISABLED]")
        if el["readonly"]:
            parts.append("[READONLY]")

        # Coordinates
        parts.append(f'@ ({el["x"]}, {el["y"]})')

        lines.append("   " + " | ".join(parts))

    if total > MAX_ELEMENTS:
        lines.append(f"   ... ({total - MAX_ELEMENTS} more elements not shown)")

    lines.append("")
    lines.append("💡 click_on_screen(x, y) to interact, send_keys(text) to type.")

    return "\n".join(lines)
