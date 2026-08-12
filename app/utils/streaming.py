"""Helpers for the guardrail-gated simulated streaming endpoint.

The stream endpoint buffers the graph's final (already guardrailed) response
and replays it in small chunks so clients still see a streaming UX even though
nothing is delivered until the outbound guardrail has approved the draft.
"""

import asyncio
import re
from typing import AsyncGenerator, List

from app.core.config import settings

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n")

_CODE_FENCE = re.compile(r"^```", re.MULTILINE)


def _is_inside_code_block(offset: int, fences: List[int]) -> bool:
    """Return whether ``offset`` falls inside a fenced code block.

    Args:
        offset: Character offset into the original text.
        fences: Line-start offsets of every ````` ``` ```` marker.

    Returns:
        True when the offset is inside an open/closed fence pair.
    """
    open_count = 0
    for fence in fences:
        if fence < offset:
            open_count += 1
    return open_count % 2 == 1


def split_response_into_chunks(text: str) -> List[str]:
    """Split a response into sentence-sized chunks without breaking code blocks.

    Chunks are split on sentence punctuation and newlines, but never inside a
    fenced code block (``` ... ```). Boundary whitespace is preserved so
    concatenating the chunks reconstructs the original text exactly.

    Args:
        text: The full assistant response to split.

    Returns:
        A list of chunk strings whose concatenation equals ``text``.
    """
    if not text:
        return []

    fence_offsets = [m.start() for m in _CODE_FENCE.finditer(text)]

    chunks: List[str] = []
    last = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        if _is_inside_code_block(match.start(), fence_offsets):
            continue
        piece = text[last : match.start()]
        if piece:
            chunks.append(piece)
        last = match.start()
    tail = text[last:]
    if tail:
        chunks.append(tail)
    return chunks


async def replay_response_chunks(text: str) -> AsyncGenerator[str, None]:
    """Yield the response in paced chunks, simulating live token streaming.

    Each chunk is yielded after a short delay (``STREAM_REPLAY_CHUNK_DELAY_MS``)
    so the client sees tokens arrive progressively. The total replay duration is
    capped by ``STREAM_REPLAY_MAX_DURATION_MS`` so very long answers do not feel
    artificially slow.

    Args:
        text: The full assistant response to replay.

    Yields:
        str: One chunk of the response at a time.
    """
    chunks = split_response_into_chunks(text)
    if not chunks:
        return

    delay_s = settings.STREAM_REPLAY_CHUNK_DELAY_MS / 1000.0
    max_duration_s = settings.STREAM_REPLAY_MAX_DURATION_MS / 1000.0
    total_slots = len(chunks) - 1
    if total_slots > 0:
        delay_s = min(delay_s, max_duration_s / total_slots)

    first = True
    for chunk in chunks:
        if not first and delay_s > 0:
            await asyncio.sleep(delay_s)
        first = False
        yield chunk
