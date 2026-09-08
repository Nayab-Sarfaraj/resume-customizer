# Resume Customizer

Tailor a LaTeX resume to a target Job Description (JD) for ATS relevance — without fabricating facts or breaking LaTeX.

Pipeline: `resume.tex` → parse units with exact character spans → extract ATS keywords (Groq + LangChain structured output) → rewrite each section in parallel → merge back with reverse-offset slicing + validation gates → `output/resume_optimized.tex`.

## Features

- **LaTeX-aware parsing** (`parser.py`): splits `\section{...}` blocks, extracts optimizable units with `(start, end)` spans:
  - `SUMMARY-*`: `\resumeItem{...}` in Summary
  - `SKILL-*`: comma-separated skill lists in Technical Skills (structural span-locking preserves `\textbf{Category}{:` prefix)
  - `EXP-{block}-{bullet}`: `\resumeItem{...}` grouped by `\resumeSubheading` company/role
  - `PROJ-{proj}-{bullet}`: `\resumeItem{...}` grouped by `\resumeProjectHeading`
  - Education is intentionally skipped. Commented lines (`%`) and escaped `\{ \} \%` are respected.
- **ATS keyword extraction** (`ats.py`): `ATSKeywords(hard_skills, soft_skills, seniority_levels)` via `ChatGroq.with_structured_output()`.
- **Section rewriting** (`section_chains.py`): one LLM chain per section (Summary, Skills, Projects, Experience batched in chunks of 4), run concurrently with `asyncio.gather`, semaphore=1 + 2s pacing + 5 retries for Groq 429 / JSON / brace errors.
- **Anti-hallucination rules**: prompt forbids inventing/altering numbers, metrics, percentages, counts. Skills are only appended 1–3 per logical category (Stack / Backend / Database / Cloud & DevOps / AI & Integrations), output as bare comma-separated list.
- **Safe merge** (`merger.py`):
  - Pre-merge gate per unit: strip redundant `\textbf{Cat}` headers, auto-repair braces (`\%` escaping, restore `{:`, balance `}`), reject on number mismatch.
  - Reverse-offset replacement (descending `start`) so earlier spans stay valid.
  - Post-merge gate: exactly one `\begin{document}` / `\end{document}`, globally balanced braces. Dumps `output/resume_merged_debug.tex` on failure.
  - Returns `MergeResult(modified_count, unchanged_count, rejected_count, rejected_ids, output_path)`.

## Project Structure

```text
resume.tex                      # base resume (input)
output/resume_optimized.tex     # tailored output
test.py                         # parser span-verification script
src/resume_customizer/
  __init__.py                   # public exports
  main.py                       # customize_resume() orchestration + CLI
  parser.py                     # parse_sections(), extract_units()
  ats.py                        # ATSKeywords, extract_ats_keywords()
  model.py                      # ChatGroq init (GROQ_MODEL, default qwen/qwen3.8-27b)
  section_chains.py             # rewrite_bullets() / arewrite_bullets()
  merger.py                     # merge_rewrites(), validate_unit(), validate_merged_latex()
  utils.py                      # read_file()
  prompt_template_generator.py  # generates ATS prompt JSON
  templates/ats_keyword_extractor_template.json
pyproject.toml
```

## Requirements

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/) (repo uses `uv.lock` + `.venv`)
- Groq API key

Dependencies: `dotenv`, `langchain`, `langchain-core`, `langchain-groq`.

## Installation

```powershell
uv sync
```

## Configuration

Create `.env` in repo root (already gitignored):

```env
GROQ_API_KEY=gsk_...
# optional override, defaults to qwen/qwen3.8-27b in model.py
GROQ_MODEL=qwen/qwen3.8-27b
```

Place your base resume at `./resume.tex` (default path expected by `main.py` and `section_chains.py`).

## Usage

### 1. Verify parser

```powershell
uv run python test.py
```

Checks section detection, unit extraction, and that every `unit.span` slices back to `unit.text` in `resume.tex`.

### 2. Run end-to-end customization

```powershell
# with sample JD built into main.py
uv run python -m resume_customizer.main

# with JD string
uv run resume-customizer "We are looking for a Senior Full Stack Engineer with Python, Next.js..."

# with JD from file
uv run resume-customizer path\to\jd.txt
```

Or from Python:

```python
from pathlib import Path
from resume_customizer import customize_resume

result = customize_resume(
    job_description=open("jd.txt", encoding="utf-8").read(),
    resume_path=Path("resume.tex"),
    output_dir=Path("output"),
)
print(result["merge_result"])
```

Pipeline console output:

```text
[1/5] Reading base resume
[2/5] Extracting ATS keywords
[3/5] Parsing LaTeX structure and optimizable units
[4/5] Running section rewriting chains in parallel
[5/5] Merging rewrites with reverse-offset slicing & validation
PIPELINE SUMMARY
Total Input Units / Modified / Unchanged / Rejected
Optimized LaTeX: output/resume_optimized.tex
```

### 3. Run section rewriting standalone

```powershell
uv run python src/resume_customizer/section_chains.py
```

Uses the sample JD + `ATSKeywords` in `__main__` and prints `[MODIFIED]` / `[UNCHANGED]` per unit.

## How It Works

1. `read_file()` loads `resume.tex`.
2. `extract_ats_keywords(jd)` → `ATSKeywords` via prompt template in `templates/ats_keyword_extractor_template.json`.
3. `extract_units(latex)` → `list[ResumeUnit{id, section, context, text, span}]` with balanced-brace scanning (`extract_braced_content`, `is_line_commented`, `strip_with_offsets`).
4. `rewrite_bullets(jd, ats_keywords, file_name)` → `dict[summary|skills|experience|projects -> SectionOutput]` via `PromptTemplate | structured_model`, `method="json_mode"`, schema `{"units": [{"id","rewritten","modified"}]}`.
5. `merge_rewrites(original_latex, units, section_results, output_path)` validates, sorts replacements descending, slices, validates whole document, writes `resume_optimized.tex`.

## Validation & Safety

- `check_braces()` / `is_braces_balanced()`: brace scan ignoring `\{ \}` and `%` comments.
- `auto_repair_unit_braces()`: escape bare `%`, restore dropped `{:`, append/trim trailing `}`.
- `validate_unit()`: brace check + `extract_numbers()` comparison (original vs rewritten must match exactly).
- `validate_merged_latex()`: single `\begin{document}` / `\end{document}` + global brace balance.
- Unrecoverable units fall back to original and are counted as rejected/unchanged, never merged broken.

## Notes / Limitations

- Input must follow the Jake Gutierrez / sb2nov template macros (`\resumeItem`, `\resumeSubheading`, `\resumeProjectHeading`, `\resumeItemListStart/End`). Other macros are ignored.
- `pyproject.toml [project.scripts]` maps `resume-customizer` to `resume_customizer:main` (works because `__init__.py` re-exports `main`). No `--help` flag exists — any CLI arg is treated as JD text or a JD file path.
- Groq TPM limit (8000) is handled by sequential execution + sleep + backoff; large Experience sections are chunked to 4 bullets per call.
- Default model is set via `GROQ_MODEL` env in `model.py` (`max_tokens=2048`).
