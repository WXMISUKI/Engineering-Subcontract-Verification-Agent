"""Extract unique excerpts from a previously verified offset-based reconstruction."""
from __future__ import annotations


def match_text_parts(document: dict, preamble: str, item: str) -> list[dict] | None:
    text, segments = document.get("text"), document.get("segments")
    if not isinstance(text, str) or not isinstance(segments, list):
        return None
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact = "".join(text[i] for i in positions)
    parts = []
    last_end = 0
    for role, expected in (("preamble", preamble), ("item", item)):
        needle = "".join(expected.split()) if isinstance(expected, str) else ""
        start = compact.find(needle) if needle else -1
        if start < 0 or compact.find(needle, start + 1) >= 0:
            return None
        begin, end = positions[start], positions[start + len(needle) - 1] + 1
        if begin < last_end:
            return None
        overlapping = [s for s in segments if s.get("start", end) < end and s.get("end", begin) > begin]
        if not overlapping or any(not isinstance(s.get("chunk_id"), str) for s in overlapping):
            return None
        covered = begin
        for segment in sorted(overlapping, key=lambda s: s["start"]):
            if segment["start"] > covered:
                return None
            covered = max(covered, segment["end"])
        if covered < end:
            return None
        ids = list(dict.fromkeys(s["chunk_id"] for s in overlapping))
        parts.append({"role": role, "chunk_id": ids[0], "chunk_ids": ids, "quote": text[begin:end]})
        last_end = end
    return parts
