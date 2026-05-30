"""
Report Generator — Creates beautiful, self-contained HTML reports of agent runs.

Injects events, durations, and base64-encoded/compressed screenshots into a sleek,
dark-themed, glassmorphic timeline report.
"""

import base64
import io
import os
import time
from pathlib import Path
from PIL import Image
from event_log import EventLog

REPORTS_DIR = Path(__file__).parent / "reports"


def compress_screenshot_to_b64(filepath: Path) -> str:
    """
    Reads a screenshot, resizes it to 512px width (maintaining aspect ratio),
    and returns its base64 JPEG encoding.
    """
    if not filepath.exists():
        return ""
    try:
        img = Image.open(filepath)
        # Maintain aspect ratio
        img.thumbnail((512, 512), Image.LANCZOS)
        # Convert to RGB (in case of PNG alpha)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=50, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception:
        return ""


def generate_report(event_log: EventLog) -> Path:
    """
    Generate a self-contained HTML report from the EventLog.
    Saves the report to the reports/ directory and returns the Path.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp_str = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(event_log.start_time))
    filename = f"run_{timestamp_str}.html"
    filepath = REPORTS_DIR / filename

    # Calculate statistics
    total_steps = max((event.step for event in event_log.events), default=0)
    total_retries = sum(1 for event in event_log.events if event.event_type == "retry")
    status_class = "status-success" if event_log.status == "success" else "status-failed"
    status_label = "✅ SUCCESS" if event_log.status == "success" else "❌ FAILED"
    
    duration = event_log.get_duration()
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    duration_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

    # Generate timeline items
    timeline_html = []
    for event in event_log.events:
        time_rel = event.timestamp - event_log.start_time
        time_rel_str = f"+{time_rel:.1f}s"
        
        # Color coding for event types
        type_class = f"badge-{event.event_type}"
        
        screenshot_html = ""
        if event.screenshot_path:
            img_path = Path(__file__).parent / event.screenshot_path
            b64_img = compress_screenshot_to_b64(img_path)
            if b64_img:
                screenshot_html = f'<img class="step-image" src="data:image/jpeg;base64,{b64_img}" alt="Screenshot">'

        # Formatting tool invocation args
        args_html = ""
        if event.tool_name:
            args_str = ", ".join(f'<strong>{k}</strong>: {v}' for k, v in (event.tool_args or {}).items())
            args_html = f'<div class="tool-call">🔧 <code>{event.tool_name}({args_str})</code></div>'

        message_lines = event.message.replace("\n", "<br>")

        timeline_html.append(f"""
        <div class="timeline-item">
            <div class="timeline-badge {type_class}">
                {event.step}
            </div>
            <div class="timeline-panel">
                <div class="timeline-heading">
                    <span class="timeline-time">{time_rel_str}</span>
                    <span class="event-badge {type_class}">{event.event_type.upper()}</span>
                </div>
                <div class="timeline-body">
                    {args_html}
                    <p class="event-msg">{message_lines}</p>
                    {screenshot_html}
                </div>
            </div>
        </div>
        """)

    timeline_content = "\n".join(timeline_html)

    # HTML/CSS Template
    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoCli Run Report - {timestamp_str}</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: rgba(22, 28, 45, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent-color: #60a5fa;
            --success: #34d399;
            --failed: #f87171;
            --warning: #fbbf24;
            --retry: #a78bfa;
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg-color);
            color: var(--text-main);
            font-family: 'Outfit', sans-serif;
            line-height: 1.6;
            padding: 40px 20px;
            background-image: radial-gradient(circle at 10% 20%, rgba(96, 165, 250, 0.05) 0%, transparent 40%),
                              radial-gradient(circle at 90% 80%, rgba(167, 139, 250, 0.05) 0%, transparent 40%);
            background-attachment: fixed;
        }}

        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}

        header {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            backdrop-filter: blur(12px);
            padding: 30px;
            border-radius: 20px;
            margin-bottom: 40px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        }}

        h1 {{
            font-size: 2.2rem;
            font-weight: 800;
            margin-bottom: 10px;
            background: linear-gradient(135deg, #fff 0%, var(--accent-color) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .task-description {{
            font-size: 1.1rem;
            color: var(--text-muted);
            margin-bottom: 25px;
            padding-left: 15px;
            border-left: 3px solid var(--accent-color);
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
        }}

        .stat-card {{
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.05);
            padding: 15px 20px;
            border-radius: 12px;
            text-align: center;
        }}

        .stat-label {{
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-muted);
            margin-bottom: 5px;
        }}

        .stat-val {{
            font-size: 1.5rem;
            font-weight: 600;
        }}

        .status-success {{
            color: var(--success);
            text-shadow: 0 0 10px rgba(52, 211, 153, 0.2);
        }}

        .status-failed {{
            color: var(--failed);
            text-shadow: 0 0 10px rgba(248, 113, 113, 0.2);
        }}

        /* Timeline styling */
        .timeline {{
            position: relative;
            padding: 20px 0;
            list-style: none;
        }}

        .timeline::before {{
            content: '';
            position: absolute;
            top: 0;
            bottom: 0;
            left: 25px;
            width: 2px;
            background-color: var(--card-border);
        }}

        .timeline-item {{
            position: relative;
            margin-bottom: 30px;
            display: flex;
            align-items: flex-start;
        }}

        .timeline-badge {{
            position: absolute;
            left: 11px;
            width: 30px;
            height: 30px;
            border-radius: 50%;
            background-color: #1f2937;
            border: 2px solid var(--card-border);
            color: var(--text-main);
            text-align: center;
            font-size: 0.9rem;
            font-weight: 600;
            line-height: 26px;
            z-index: 10;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .timeline-badge.badge-thinking {{
            border-color: var(--warning);
            color: var(--warning);
            box-shadow: 0 0 8px rgba(251, 191, 36, 0.3);
        }}

        .timeline-badge.badge-tool_call {{
            border-color: var(--accent-color);
            color: var(--accent-color);
            box-shadow: 0 0 8px rgba(96, 165, 250, 0.3);
        }}

        .timeline-badge.badge-observation {{
            border-color: var(--success);
            color: var(--success);
            box-shadow: 0 0 8px rgba(52, 211, 153, 0.3);
        }}

        .timeline-badge.badge-retry {{
            border-color: var(--retry);
            color: var(--retry);
            box-shadow: 0 0 8px rgba(167, 139, 250, 0.3);
        }}

        .timeline-badge.badge-error {{
            border-color: var(--failed);
            color: var(--failed);
            box-shadow: 0 0 8px rgba(248, 113, 113, 0.3);
        }}

        .timeline-panel {{
            margin-left: 60px;
            flex-grow: 1;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 20px;
            position: relative;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            transition: transform 0.2s ease, border-color 0.2s ease;
        }}

        .timeline-panel:hover {{
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.15);
        }}

        .timeline-heading {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 8px;
        }}

        .timeline-time {{
            font-size: 0.85rem;
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
        }}

        .event-badge {{
            font-size: 0.7rem;
            padding: 3px 8px;
            border-radius: 6px;
            font-weight: 600;
            letter-spacing: 0.5px;
        }}

        .event-badge.badge-thinking {{ background: rgba(251, 191, 36, 0.15); color: var(--warning); }}
        .event-badge.badge-tool_call {{ background: rgba(96, 165, 250, 0.15); color: var(--accent-color); }}
        .event-badge.badge-observation {{ background: rgba(52, 211, 153, 0.15); color: var(--success); }}
        .event-badge.badge-retry {{ background: rgba(167, 139, 250, 0.15); color: var(--retry); }}
        .event-badge.badge-error {{ background: rgba(248, 113, 113, 0.15); color: var(--failed); }}

        .tool-call {{
            background: rgba(0, 0, 0, 0.25);
            border-radius: 8px;
            padding: 10px 15px;
            margin-bottom: 12px;
            border-left: 3px solid var(--accent-color);
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
        }}

        .tool-call strong {{
            color: var(--accent-color);
        }}

        .event-msg {{
            color: var(--text-main);
            font-size: 0.95rem;
            margin-bottom: 15px;
            white-space: pre-wrap;
        }}

        .step-image {{
            width: 100%;
            max-width: 600px;
            border-radius: 8px;
            border: 1px solid var(--card-border);
            display: block;
            margin-top: 15px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            transition: transform 0.2s;
        }}
        
        .step-image:hover {{
            transform: scale(1.02);
            cursor: zoom-in;
        }}

        footer {{
            text-align: center;
            color: var(--text-muted);
            margin-top: 50px;
            font-size: 0.9rem;
            border-top: 1px solid var(--card-border);
            padding-top: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>AutoCli Agent Run Summary</h1>
            <p class="task-description">{event_log.task_description}</p>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Status</div>
                    <div class="stat-val {status_class}">{status_label}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Duration</div>
                    <div class="stat-val">{duration_str}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total Steps</div>
                    <div class="stat-val">{total_steps}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Self-Heals</div>
                    <div class="stat-val" style="color: var(--retry);">{total_retries}</div>
                </div>
            </div>
        </header>

        <main>
            <div class="timeline">
                {timeline_content}
            </div>
        </main>

        <footer>
            Generated by AutoCli Automation Suite • {timestamp_str}
        </footer>
    </div>
</body>
</html>
"""

    filepath.write_text(html_template, encoding="utf-8")
    return filepath
