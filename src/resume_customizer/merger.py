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


def check_braces(text: str) -> Tuple[bool, int, int]:
    r"""
    Scans text for curly braces, respecting escaped braces \{ and \} and comments %.
    Returns:
        (is_balanced, final_depth, min_depth)
    """
    depth = 0
    min_depth = 0
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
                return False, depth, min_depth
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
            if depth < min_depth:
                min_depth = depth

        i += 1

    return (depth == 0 and min_depth >= 0), depth, min_depth


def is_braces_balanced(text: str) -> bool:
    r"""Checks if curly braces are balanced, respecting escaped braces \{ and \}."""
    balanced, _, _ = check_braces(text)
    return balanced


def escape_unescaped_percent(text: str) -> str:
    r"""Escapes any literal '%' that is not already escaped as '\%' in LaTeX."""
    return re.sub(r'(?<!\\)%', r'\%', text)


def auto_repair_unit_braces(original_text: str, rewritten_text: str) -> str:
    r"""
    Attempts to heal minor brace imbalances produced by LLMs on LaTeX units.
    Common LLM slips:
    1. Unescaped '%' commenting out the rest of the line and trailing braces -> escapes as '\%'
    2. Dropping '{:' after \textbf{Category} -> restores '{:'
    3. Forgetting the outer closing brace of `{: ...}` -> appends missing '}'
    4. Adding an extra trailing '}' -> trims extraneous trailing '}'
    """
    repaired = rewritten_text.strip()

    # Step 1: Escape unescaped '%' (in LaTeX, unescaped % comments out closing braces!)
    if "%" in repaired:
        repaired = escape_unescaped_percent(repaired)

    is_bal, depth, min_depth = check_braces(repaired)
    if is_bal:
        return repaired

    # Step 2: Dropped '{:' after \textbf{...}
    cat_match = re.match(r'(\\textbf\{[^}]+\})\{:', original_text.strip())
    if cat_match:
        prefix = cat_match.group(1)
        if repaired.startswith(prefix + ":"):
            candidate = prefix + "{:" + repaired[len(prefix) + 1:]
            if check_braces(candidate)[0]:
                return candidate
            repaired = candidate

    is_bal, depth, min_depth = check_braces(repaired)
    if is_bal:
        return repaired

    # Step 3: Missing closing brace(s) at end (depth > 0, min_depth >= 0)
    if depth > 0 and min_depth >= 0:
        candidate = repaired + ("}" * depth)
        if check_braces(candidate)[0]:
            return candidate

    # Step 4: Extra trailing brace(s) (depth < 0)
    if depth < 0 and repaired.endswith("}"):
        for trim in range(1, abs(depth) + 1):
            if repaired.endswith("}" * trim):
                candidate = repaired[:-trim]
                if check_braces(candidate)[0]:
                    return candidate

    return rewritten_text


def extract_numbers(text: str) -> List[str]:
    """Extracts all numbers (integers and decimals) from text."""
    return re.findall(r'\b\d+(?:\.\d+)?\b', text)


def validate_unit(unit_id: str, original_text: str, rewritten_text: str) -> Tuple[bool, str, str]:
    """
    Pre-merge validation gate for an individual unit:
    1. For skills: strips redundant category header if the model accidentally repeated it.
    2. Confirm curly brace count is balanced (auto-repairs minor slips if possible).
    3. Confirm no new or altered numbers/metrics vs original.
    Returns (is_valid, reason, validated_text).
    """
    candidate_text = rewritten_text

    # Defense-in-depth: If a SKILL unit redundantly contains the category header (e.g. \textbf{Stack}{: ...)
    if unit_id.startswith("SKILL-"):
        redundant_cat = re.match(r'\\textbf\{[^}]+\}\s*\{?:\s*(.*)', candidate_text, re.DOTALL)
        if redundant_cat:
            inner_candidate = redundant_cat.group(1).rstrip()
            if inner_candidate.endswith("}") and not original_text.rstrip().endswith("}"):
                inner_candidate = inner_candidate[:-1].rstrip()
            candidate_text = inner_candidate

    if not is_braces_balanced(candidate_text):
        repaired = auto_repair_unit_braces(original_text, candidate_text)
        if is_braces_balanced(repaired):
            print(f"[PRE-MERGE GATE] [{unit_id}] Auto-repaired curly braces successfully.")
            candidate_text = repaired
        else:
            return False, f"[{unit_id}] REJECTED: Curly braces are unbalanced in rewritten text.", rewritten_text

    orig_numbers = sorted(extract_numbers(original_text))
    new_numbers = sorted(extract_numbers(candidate_text))

    if orig_numbers != new_numbers:
        return (
            False,
            f"[{unit_id}] REJECTED: Number/metric mismatch. Original numbers: {orig_numbers}, Rewritten numbers: {new_numbers}",
            candidate_text
        )

    return True, "OK", candidate_text


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

        clean_id = rew.id.strip("[]").strip()
        if clean_id not in units_by_id:
            print(f"[WARNING] Unknown unit id '{rew.id}' skipped.")
            unchanged_count += 1
            continue

        original_unit = units_by_id[clean_id]
        orig_text = original_unit["text"]

        # Pre-merge validation gate
        is_valid, reason, valid_text = validate_unit(clean_id, orig_text, rew.rewritten)
        if not is_valid:
            print(f"[PRE-MERGE GATE] {reason} -> Falling back to original.")
            rejected_ids.append(clean_id)
            unchanged_count += 1
            continue

        start, end = original_unit["span"]
        replacements.append((clean_id, start, end, valid_text))

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
