import sys
from pathlib import Path

def render(raw_path):
    raw_text = Path(raw_path).read_text(encoding="utf-8")

    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ReflectIQ – Level B Report</title>
<style>
body {{
    font-family: Arial, sans-serif;
    max-width: 900px;
    margin: 40px auto;
    line-height: 1.6;
}}
h1, h2, h3 {{
    margin-top: 1.6em;
}}
</style>
</head>
<body>
<pre style="white-space: pre-wrap;">{raw_text}</pre>
</body>
</html>
"""

    out = Path(raw_path).with_name("levelB_report.html")
    out.write_text(html, encoding="utf-8")
    print(f"✔ Wrote: {out}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python levelB_renderer.py LevelB_output_raw.txt")
        sys.exit(1)

    render(sys.argv[1])