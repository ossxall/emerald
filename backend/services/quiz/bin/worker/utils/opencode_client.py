"""Small subprocess client for OpenCode's authenticated CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TextIO


WORKER_PATH = Path(__file__).resolve().parents[1]
MAX_INLINE_PROMPT_BYTES = 64 * 1024


class OpenCodeError(RuntimeError):
    """Raised when OpenCode cannot produce a response."""


def _response_text(output: str) -> str:
    """Extract assistant text from OpenCode's newline-delimited JSON output."""
    text_parts: list[str] = []
    parsed_event = False

    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue
        parsed_event = True

        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)

    if text_parts:
        return "".join(text_parts).strip()

    # Keep support for versions that return plain text despite --format json.
    if not parsed_event:
        return output.strip()
    return ""


def _cli_command(prompt: str) -> tuple[list[str], TextIO | None]:
    executable = os.environ.get("OPENCODE_BIN") or shutil.which("opencode")
    if not executable:
        raise OpenCodeError(
            "No se encontró la CLI de OpenCode. Instálala o configura OPENCODE_BIN."
        )

    command = [executable, "run", "--format", "json"]
    agent = os.environ.get("OPENCODE_AGENT", "quiz").strip()
    model = os.environ.get("OPENCODE_MODEL", "").strip()
    if agent:
        command.extend(["--agent", agent])
    if model:
        command.extend(["--model", model])

    prompt_file = None
    if len(prompt.encode("utf-8")) > MAX_INLINE_PROMPT_BYTES:
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".txt", prefix="opencode-prompt-"
        )
        prompt_file.write(prompt)
        prompt_file.flush()
        command.extend(
            [
                "--file",
                prompt_file.name,
                "Sigue las instrucciones y usa el contenido del archivo adjunto.",
            ]
        )
    else:
        command.append(prompt)

    return command, prompt_file


def opencode_completion(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int | None = None,
) -> str:
    """Request a completion through OpenCode and return assistant text.

    OpenCode's CLI does not expose a hard output-token limit, so ``max_tokens``
    is included as a target in the prompt rather than enforced by the CLI.
    """
    prompt = (
        f"Instrucciones del sistema:\n{system_prompt.strip()}\n\n"
        f"Solicitud:\n{user_prompt.strip()}"
    )
    if max_tokens is not None:
        prompt += f"\n\nObjetivo de longitud: no excedas aproximadamente {max_tokens} tokens."

    command, prompt_file = _cli_command(prompt)
    timeout = int(os.environ.get("OPENCODE_TIMEOUT_SECONDS", "900"))
    child_env = os.environ.copy()
    # These application credentials are not needed by OpenCode.
    for key in list(child_env):
        if key.startswith("S3_") or key == "GEMINI_API_KEY":
            child_env.pop(key, None)

    try:
        result = subprocess.run(
            command,
            cwd=WORKER_PATH,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenCodeError(
            f"OpenCode excedió el tiempo límite de {timeout} segundos."
        ) from exc
    except OSError as exc:
        raise OpenCodeError(f"No se pudo ejecutar OpenCode: {exc}") from exc
    finally:
        if prompt_file is not None:
            prompt_file.close()

    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        if len(details) > 2000:
            details = details[-2000:]
        raise OpenCodeError(
            f"OpenCode terminó con código {result.returncode}. {details}"
        )

    response = _response_text(result.stdout)
    if not response:
        raise OpenCodeError("OpenCode terminó sin devolver texto.")
    return response
