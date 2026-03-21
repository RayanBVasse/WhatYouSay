import os
import sys
import json
import time
from pathlib import Path
from openai import OpenAI

from levelB_prompt import build_levelB_prompt

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"

# If you insist on hard-coding, put it here (NOT recommended for deploy).
# Better: set environment variable OPENAI_API_KEY.

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in environment")
client = OpenAI(api_key=OPENAI_API_KEY)

def generate_levelB_narrative(*,anon_text: str,self_text: str,metrics: dict,evidence: dict | None = None,speaker_alias: str,) -> dict:
    """
    Pure in-memory Level B generator.
    Returns parsed JSON (dict).
    Safe for Flask / Render / sessions.
    """

    evidence = evidence or {}

    prompt = build_levelB_prompt(
        anon_text=anon_text,
        self_text=self_text,
        metrics=metrics,
        evidence=evidence,
        speaker_alias=speaker_alias,
    )

    raw = call_openai(prompt)

    try:
        parsed = json.loads(raw)
    except Exception as e:
        raise RuntimeError("Level B JSON parse failed") from e

    return parsed

def load_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))

def try_load_evidence(input_dir: Path) -> dict:
    # Optional: load evidence_levelA.csv or evidence_levelA.json if you later add it
    ev_json = input_dir / "evidence_levelA.json"
    if ev_json.exists():
        return load_json(ev_json)
    return {}

def call_openai(prompt: str) -> str:
    """
    Uses OpenAI python SDK if installed. If not, you’ll get a clear error.
    """
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("OpenAI SDK not installed. Run: pip install openai") from e

    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY. Set it in environment or hardcode OPENAI_API_KEY.")


    # Choose your model. Keep it stable.
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    resp = client.responses.create(
        model=model,
        input=prompt,
        # Important: we asked for JSON-only in prompt; this just keeps it clean
        temperature=0.4,
    )

    # Responses API returns output text in a few possible shapes; this is a safe extraction:
    out_text = ""
    for item in resp.output:
        if item.type == "message":
            for c in item.content:
                if c.type in ("output_text", "text"):
                    out_text += c.text
    return out_text.strip()

def main():
    # Windows console can choke on unicode markers; keep prints plain ASCII.
    print("Level B runner started")

    if len(sys.argv) < 2:
        print("Usage: python levelB_runner.py <safe_user>")
        sys.exit(1)

    safe_user = sys.argv[1].strip()
    if not safe_user:
        print("Error: safe_user empty")
        sys.exit(1)

    input_dir = Path("results") / safe_user
    print(f"INPUT_DIR: {input_dir}")

    anon_path = input_dir / f"{safe_user}_anonymized_chat.txt"
    self_path = input_dir / f"{safe_user}_only_chat.txt"
    metrics_path = input_dir / "metrics_levelA.json"

    # Require files
    for p in [anon_path, self_path, metrics_path]:
        if not p.exists():
            print(f"Missing: {p}")
            sys.exit(1)

    anon_text = load_text(anon_path)
    self_text = load_text(self_path)
    metrics = load_json(metrics_path)
    evidence = try_load_evidence(input_dir)

    # Build prompt
    prompt = build_levelB_prompt(
        anon_text=anon_text,
        self_text=self_text,
        metrics=metrics,
        evidence=evidence,
        speaker_alias=safe_user,
    )

    prompt_path = input_dir / "levelB_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"Prompt written: {prompt_path}")

    t0 = time.time()
    print("LEVEL B START")
    
    # Call LLM
    print("Calling OpenAI...")
    raw = call_openai(prompt)

    raw_path = input_dir / "levelB_output_raw.txt"
    raw_path.write_text(raw, encoding="utf-8")
    print(f"Level B output written: {raw_path}")

    # Parse JSON output
    try:
        parsed = json.loads(raw)
    except Exception as e:
        # Save a debug marker so you can see it failed parse
        bad_path = input_dir / "levelB_output_PARSE_FAILED.txt"
        bad_path.write_text(f"JSON parse failed: {e}\n\nRAW:\n{raw}", encoding="utf-8")
        print("JSON parse failed. Wrote levelB_output_PARSE_FAILED.txt")
        sys.exit(1)

    sections = parsed.get("sections", [])
    blocks = []

    for s in sections:
        blocks.append(f"## {s['title']}\n\n{s['body']}")
        if s.get("highlights"):
            blocks.append("\n".join([f"- {h}" for h in s["highlights"]]))

    final_text = "\n\n".join(blocks)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(final_text)

    json_path = input_dir / "levelB_output.json"
    json_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Parsed JSON written: {json_path}")

    print("Level B runner finished")

if __name__ == "__main__":
    main()





