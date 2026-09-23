"""
summarizer.py
Hierarchical cascade summarizer using a liteLLM router.
Accepts a markdown document (str | bytes) and returns 3 prose paragraphs
in the language specified by the `language` argument.
"""

from __future__ import annotations

import asyncio
from typing import Union

from langchain_text_splitters import RecursiveCharacterTextSplitter
from litellm.router import Router

# ── Router setup ──────────────────────────────────────────────────────────────

model_list = [
    {
        "model_name": "models-1",
        "litellm_params": {"model": "gemini/gemini-3.1-flash-lite"},
    },
    {
        "model_name": "models-1",
        "litellm_params": {
            "model": "ollama/llama3.2:3b",
            "api_base": "http://localhost:11434",
        },
    },
]

router = Router(
    model_list=model_list,
    num_retries=6,
    retry_after=4,
    allowed_fails=2,
)

# ── Pipeline constants ────────────────────────────────────────────────────────

CHUNK_CHARS        = 12_000
CHUNK_OVERLAP      = 200
MAX_CHUNKS         = 40
MERGE_BATCH_SIZE   = 8
MINI_SUMMARY_SENTS = 3
MAX_CONCURRENCY    = 4

# ── Splitter (singleton) ──────────────────────────────────────────────────────

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_CHARS,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " "],
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _decode(document: Union[str, bytes]) -> str:
    if isinstance(document, (bytes, bytearray)):
        return document.decode("utf-8", errors="replace")
    return document


def _split_chunks(text: str) -> list[str]:
    return _splitter.split_text(text)[:MAX_CHUNKS]


# ── Async LLM wrapper ─────────────────────────────────────────────────────────


async def _llm(system: str, user: str, max_tokens: int = 512) -> str:
    response = await router.acompletion(
        model="models-1",
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


# ── Pipeline stages ───────────────────────────────────────────────────────────


async def _summarize_chunk(
    chunk: str, index: int, total: int, language: str, sem: asyncio.Semaphore
) -> str:
    system = (
        f"You are an expert summarizer. Always respond in {language}. "
        "Be concise and factual. Output only plain prose, no lists or markdown."
    )
    user = (
        f"Chunk {index + 1} of {total}:\n\n{chunk}\n\n"
        f"Write exactly {MINI_SUMMARY_SENTS} sentences capturing the key ideas."
    )
    async with sem:
        return await _llm(system, user, max_tokens=200)


async def _merge_batch(summaries: list[str], language: str) -> str:
    joined = "\n".join(f"[{i + 1}] {s}" for i, s in enumerate(summaries))
    system = (
        f"You are an expert at synthesizing information. Always respond in {language}. "
        "Output only plain prose, no lists or markdown."
    )
    user = (
        "Merge these section summaries into a single coherent paragraph "
        f"(max 150 words), removing repetition:\n\n{joined}"
    )
    return await _llm(system, user, max_tokens=300)


async def _intermediate_summary(merged_blocks: list[str], language: str) -> str:
    joined = "\n\n".join(merged_blocks)
    system = (
        f"You are an expert summarizer. Always respond in {language}. "
        "Output only plain prose, no lists or markdown."
    )
    user = (
        "Consolidate the following blocks into a single coherent summary "
        f"(max 300 words), preserving the main argument and key findings:\n\n{joined}"
    )
    return await _llm(system, user, max_tokens=500)


async def _final_three_paragraphs(intermediate: str, language: str) -> str:
    system = (
        f"You are a skilled writer. Always respond in {language}. "
        "Output only plain prose paragraphs — no titles, no lists, no markdown, no numbering."
    )
    user = (
        "Using the summary below, write exactly 3 paragraphs separated by a blank line.\n\n"
        "Rules:\n"
        "- Paragraph 1 (4-5 sentences): context and central theme.\n"
        "- Paragraph 2 (4-5 sentences): main arguments or findings.\n"
        "- Paragraph 3 (3-4 sentences): conclusions or takeaways.\n"
        "- No intro sentence, no commentary outside the 3 paragraphs.\n\n"
        f"Summary:\n{intermediate}"
    )
    return await _llm(system, user, max_tokens=600)


# ── Core async pipeline ───────────────────────────────────────────────────────


async def summarize_to_three_paragraphs(
    document: Union[str, bytes],
    language: str = "English",
    verbose: bool = False,
    bypass: bool = False,
) -> str | None:
    """
    Async entrypoint. Use this directly when inside FastAPI, async workers,
    or any context with a running event loop:

        result = await summarize_to_three_paragraphs(doc, language="Spanish")

    Args:
        document: Full markdown text (str or UTF-8 bytes).
        language: Target language for all outputs, e.g. "English", "Spanish".
        verbose:  Print progress to stdout when True.

    Returns:
        Three prose paragraphs separated by a blank line, in `language`.
    """
    
    if bypass:
        return None
    
    text = _decode(document)
    if not text.strip():
        raise ValueError("Document is empty.")

    chunks = _split_chunks(text)
    total  = len(chunks)

    if verbose:
        print(f"📄 {len(text):,} chars → {total} chunk(s) | language: {language}")

    # Stage 1: parallel chunk summarization
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    mini_summaries: list[str] = await asyncio.gather(*[
        _summarize_chunk(chunk, i, total, language, sem)
        for i, chunk in enumerate(chunks)
    ])

    if verbose:
        print(f"  ✓ {total} chunk(s) summarized")

    # Stage 2: hierarchical merge
    if total == 1:
        intermediate = mini_summaries[0]
    else:
        batches = [
            mini_summaries[i : i + MERGE_BATCH_SIZE]
            for i in range(0, total, MERGE_BATCH_SIZE)
        ]
        if verbose:
            print(f"🔗 Merging in {len(batches)} batch(es)…")

        merged_blocks: list[str] = await asyncio.gather(*[
            _merge_batch(batch, language) for batch in batches
        ])

        intermediate = (
            merged_blocks[0]
            if len(merged_blocks) == 1
            else await _intermediate_summary(list(merged_blocks), language)
        )

    # Stage 3: final output
    if verbose:
        print("✍️  Generating final 3 paragraphs…")

    return await _final_three_paragraphs(intermediate, language)


