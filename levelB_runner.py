import json
import os
import sys
import time
from pathlib import Path

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from levelB_prompt import build_levelB_prompt

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"


def _client() -> OpenAI:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in environment.")

    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "90"))
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
    return OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries)


def _extract_output_text(resp) -> str:
    # Newer SDKs provide output_text directly.
    out_text = getattr(resp, "output_text", None)
    if out_text:
        return str(out_text).strip()

    chunks = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for c in getattr(item, "content", []) or []:
            ctype = getattr(c, "type", None)
            if ctype in ("output_text", "text"):
                text = getattr(c, "text", "")
                if text:
                    chunks.append(str(text))

    return "\n".join(chunks).strip()


def call_openai(prompt: str) -> str:
    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()
    attempts = max(1, int(os.getenv("OPENAI_ATTEMPTS", "3")))
    client = _client()

    for attempt in range(1, attempts + 1):
        try:
            resp = client.responses.create(
                model=model,
                input=prompt,
                temperature=0.4,
            )
            out = _extract_output_text(resp)
            if not out:
                raise RuntimeError("OpenAI returned empty output.")
            return out
        except (APIConnectionError, APITimeoutError) as e:
            if attempt >= attempts:
                raise RuntimeError(
                    f"OpenAI connection timed out after {attempts} attempts. "
                    "Try again in 1-2 minutes or with a smaller upload."
                ) from e
            time.sleep(1.5 * attempt)
        except AuthenticationError as e:
            raise RuntimeError(
                "OpenAI authentication failed. Check OPENAI_API_KEY in Vercel env vars."
            ) from e
        except RateLimitError as e:
            raise RuntimeError("OpenAI rate limit reached. Please wait and retry.") from e
        except BadRequestError as e:
            raise RuntimeError(f"OpenAI rejected the request: {e}") from e
        except APIStatusError as e:
            raise RuntimeError(f"OpenAI API error (status {e.status_code}).") from e
        except Exception as e:
            raise RuntimeError(f"OpenAI call failed: {e}") from e


def generate_levelB_narrative(*, anon_text: str, self_text: str, metrics: dict, evidence: dict | None = None, speaker_alias: str) -> dict:
    """
    Pure in-memory Level B generator.
    Returns parsed JSON (dict).
    Safe for Flask / Vercel sessions.
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
    ev_json = input_dir / "evidence_levelA.json"
    if ev_json.exists():
        return load_json(ev_json)
    return {}


def main():
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

    for p in [anon_path, self_path, metrics_path]:
        if not p.exists():
            print(f"Missing: {p}")
            sys.exit(1)

    anon_text = load_text(anon_path)
    self_text = load_text(self_path)
    metrics = load_json(metrics_path)
    evidence = try_load_evidence(input_dir)

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

    print("Calling OpenAI...")
    raw = call_openai(prompt)

    raw_path = input_dir / "levelB_output_raw.txt"
    raw_path.write_text(raw, encoding="utf-8")
    print(f"Level B output written: {raw_path}")

    try:
        parsed = json.loads(raw)
    except Exception as e:
        bad_path = input_dir / "levelB_output_PARSE_FAILED.txt"
        bad_path.write_text(f"JSON parse failed: {e}\n\nRAW:\n{raw}", encoding="utf-8")
        print("JSON parse failed. Wrote levelB_output_PARSE_FAILED.txt")
        sys.exit(1)

    json_path = input_dir / "levelB_output.json"
    json_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Parsed JSON written: {json_path}")

    print("Level B runner finished")


if __name__ == "__main__":
    main()
