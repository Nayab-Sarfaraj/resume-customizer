import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from pydantic import BaseModel, Field

from resume_customizer.parser import ResumeUnit
from resume_customizer.section_chains import RewrittenUnit, SectionOutput


class MergeResult(BaseModel):
    merged_text: str
    output_path: Path
    modified_count: int
    unchanged_count: int
    rejected_count: int
    rejected_ids: List[str] = Field(default_factory=list)


def is_braces_balanced(text: str) -> bool:
    r"""Checks if curly braces are balanced, respecting escaped braces \{ and \}."""
    depth = 0
    i = 0
    in_comment = False

    while i < len(text):
        ch = text[i]

        if in_comment:
            if ch == '\n':
                in_comment = False
            i += 1
            continue

        if ch == '\\':
            # Guard against lone trailing backslash
            if i + 1 >= len(text):
                return False
            i += 2
            continue

        if ch == '%':
            in_comment = True
            i += 1
            continue

        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth < 0:
                return False

        i += 1

    return depth == 0


def extract_numbers(text: str) -> List[str]:
    """Extracts all numbers (integers and decimals) from text."""
    return re.findall(r'\b\d+(?:\.\d+)?\b', text)


def validate_unit(unit_id: str, original_text: str, rewritten_text: str) -> Tuple[bool, str]:
    """
    Pre-merge validation gate for an individual unit:
    1. Confirm curly brace count is balanced.
    2. Confirm no new or altered numbers/metrics vs original.
    """
    if not is_braces_balanced(rewritten_text):
        return False, f"[{unit_id}] REJECTED: Curly braces are unbalanced in rewritten text."

    orig_numbers = sorted(extract_numbers(original_text))
    new_numbers = sorted(extract_numbers(rewritten_text))

    if orig_numbers != new_numbers:
        return (
            False,
            f"[{unit_id}] REJECTED: Number/metric mismatch. Original numbers: {orig_numbers}, Rewritten numbers: {new_numbers}"
        )

    return True, "OK"


def validate_merged_latex(latex_text: str) -> Tuple[bool, str]:
    """
    Post-merge structural sanity check for the whole document:
    1. Exactly one \\begin{document} and \\end{document}.
    2. Globally balanced curly braces.
    """
    begin_count = latex_text.count(r"\begin{document}")
    if begin_count != 1:
        return False, f"Expected exactly 1 '\\begin{{document}}', found {begin_count}."

    end_count = latex_text.count(r"\end{document}")
    if end_count != 1:
        return False, f"Expected exactly 1 '\\end{{document}}', found {end_count}."

    if not is_braces_balanced(latex_text):
        return False, "Global curly brace count is unbalanced across the merged document."

    return True, "OK"


def merge_rewrites(
    original_latex: str,
    units: List[ResumeUnit],
    section_results: Dict[str, SectionOutput],
    output_path: Optional[Path] = None,
    debug_path: Optional[Path] = None,
) -> MergeResult:
    """
    Merges rewritten units back into the original LaTeX text using reverse-offset slicing.
    
    1. Collects all modified units from section_results.
    2. Runs pre-merge validation gate (brace balance & number preservation).
    3. Sorts replacements descending by start offset.
    4. Slices and replaces in reverse order to preserve earlier offsets.
    5. Runs post-merge sanity checks.
    6. Writes merged file to output_path.
    7. Returns a structured MergeResult summary.
    """
    if output_path is None:
        output_path = Path(__file__).resolve().parents[2] / "output" / "resume_optimized.tex"
    if debug_path is None:
        debug_path = Path(__file__).resolve().parents[2] / "output" / "resume_merged_debug.tex"

    units_by_id = {u["id"]: u for u in units}

    # Flatten all rewritten units across sections
    all_rewritten: List[RewrittenUnit] = []
    for sec_out in section_results.values():
        all_rewritten.extend(sec_out.units)

    replacements = []
    rejected_ids = []
    unchanged_count = 0

    for rew in all_rewritten:
        if not rew.modified:
            unchanged_count += 1
            continue

        if rew.id not in units_by_id:
            print(f"[WARNING] Unknown unit id '{rew.id}' skipped.")
            unchanged_count += 1
            continue

        original_unit = units_by_id[rew.id]
        orig_text = original_unit["text"]

        # Pre-merge validation gate
        is_valid, reason = validate_unit(rew.id, orig_text, rew.rewritten)
        if not is_valid:
            print(f"[PRE-MERGE GATE] {reason} -> Falling back to original.")
            rejected_ids.append(rew.id)
            unchanged_count += 1
            continue

        start, end = original_unit["span"]
        replacements.append((rew.id, start, end, rew.rewritten))

    # Sort descending by start offset (CRITICAL: prevents offset corruption)
    replacements.sort(key=lambda x: x[1], reverse=True)

    print(f"Applying {len(replacements)} verified replacement(s) in reverse offset order...")

    merged = original_latex
    for uid, start, end, new_text in replacements:
        print(f"  * Replacing {uid} at span ({start}, {end})")
        merged = merged[:start] + new_text + merged[end:]

    # Post-merge structural sanity check
    sanity_ok, sanity_reason = validate_merged_latex(merged)
    if not sanity_ok:
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(merged, encoding="utf-8")
        raise ValueError(
            f"Post-merge sanity check failed: {sanity_reason}. "
            f"Dumped faulty LaTeX to {debug_path} for inspection."
        )

    # Write merged LaTeX to output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(merged, encoding="utf-8")
    print(f"[SUCCESS] Merged LaTeX validated and written to: {output_path}")

    return MergeResult(
        merged_text=merged,
        output_path=output_path,
        modified_count=len(replacements),
        unchanged_count=unchanged_count,
        rejected_count=len(rejected_ids),
        rejected_ids=rejected_ids,
    )
