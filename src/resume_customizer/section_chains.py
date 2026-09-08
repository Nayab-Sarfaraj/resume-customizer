import asyncio
from pathlib import Path
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate

from resume_customizer.model import model
from resume_customizer.parser import extract_units, ResumeUnit
from resume_customizer.ats import ATSKeywords, extract_ats_keywords
from resume_customizer.utils import read_file


class RewrittenUnit(BaseModel):
    id: str = Field(description="Must match the exact input ID (e.g. EXP-0-1)")
    rewritten: str = Field(description="The tailored, LaTeX-safe text for this unit")
    modified: bool = Field(default=False, description="True if changes were made, False if left unchanged")


class SectionOutput(BaseModel):
    units: list[RewrittenUnit]


structured_model = model.with_structured_output(SectionOutput, method="json_mode")

template = PromptTemplate(
    input_variables=["section_name", "job_description", "hard_skills", "soft_skills", "units_to_rewrite"],
    template="""You are optimizing one section of a LaTeX resume to better match a job description, without ever fabricating or altering facts.

SECTION: {section_name}

JOB DESCRIPTION:
{job_description}

RELEVANT KEYWORDS TO CONSIDER:
Hard skills: {hard_skills}
Soft skills: {soft_skills}

UNITS TO REWRITE (id: original text):
{units_to_rewrite}

RULES — NON-NEGOTIABLE:
1. Return exactly one rewritten unit per input id. Never add, remove, or merge ids.
2. Tailor phrasing, framing, tools, and technical achievements to maximize ATS relevance.
   CRITICAL METRIC RULE: NEVER invent, add, or alter any numbers, percentages, or quantitative metrics (e.g. latency numbers, user counts, percentages, team sizes, dollar amounts). All numbers from the original unit must remain strictly unchanged. If the original bullet has no numbers, do NOT introduce any. Any unit with new or modified numbers will fail automated validation and be discarded.
3. Preserve every LaTeX command exactly as-is — \\textbf{{}}, \\%, $\\sim$, →, and all brace structure. You are rewriting the English content inside commands, not the LaTeX syntax itself.
4. Preserve original spacing/formatting quirks around commands even if they look inconsistent.
5. If a unit has nothing relevant to improve for this job description, return it unchanged with "modified": false rather than forcing an irrelevant keyword in.
6. For Technical Skills units specifically:
   - Input format: Each unit is purely a comma-separated list of skills belonging to that category (e.g. "\\textbf{{Node.js}}, \\textbf{{Express.js}}, REST APIs...").
   - Categorization: Only append relevant new skills to their LOGICAL category:
     * Stack: Core programming languages, full stack frameworks, runtime/OS environments (e.g. Python, TypeScript, React, Linux).
     * Backend: Server frameworks, databases/queues, backend API tools (e.g. FastAPI, BullMQ, Docker).
     * Database: Databases and caching systems (e.g. Redis, PostgreSQL).
     * Cloud & DevOps: CI/CD, cloud providers, containerization, CLI/OS (e.g. Docker, Linux, Bash, Git).
     * AI & Integrations: AI/LLM tools, SDKs, agent libraries (e.g. Cursor, Claude code, LangChain).
     Never dump all keywords blindly into every category. Only append 1-3 highly relevant keywords to the matching category.
   - Output format: Return ONLY the comma-separated list of skills. Do NOT output category headers (e.g. do not output "\\textbf{{Stack}}{{:") or outer enclosing braces.
   - For bold items, use \\textbf{{Item}}. Make sure every "\\textbf{{" has a matching "}}".

Respond with a valid JSON object matching this schema:
{{"units": [{{"id": "...", "rewritten": "...", "modified": false}}]}}
"""
)

section_chain = template | structured_model


def format_units_to_rewrite(section_units: List[ResumeUnit]) -> str:
    """Formats a list of section units into a clean text block for the prompt."""
    lines = []
    for u in section_units:
        lines.append(f"[{u['id']}] (Context: {u['context']})\nOriginal: {u['text']}")
    return "\n\n".join(lines)


def chunk_units(units: List[ResumeUnit], chunk_size: int = 4) -> List[List[ResumeUnit]]:
    """Splits a list of units into smaller batches to respect LLM token budgets and rate limits."""
    return [units[i:i + chunk_size] for i in range(0, len(units), chunk_size)]


async def arewrite_bullets(
    job_description: str,
    ats_keywords: Optional[ATSKeywords] = None,
    file_name: Path = Path(__file__).resolve().parents[2] / "resume.tex"
) -> Dict[str, SectionOutput]:
    """
    Asynchronously runs section-specific rewriting chains with sub-batching for large sections
    and rate-limiting governance to stay safely within Groq TPM limits.
    """
    if ats_keywords is None:
        ats_keywords = extract_ats_keywords(job_description)

    original_latex = read_file(file_name=file_name)
    units = extract_units(original_latex)

    # Filter units by section
    summary_units    = [u for u in units if u["section"] == "Summary"]
    skills_units     = [u for u in units if u["section"] == "Technical Skills"]
    experience_units = [u for u in units if u["section"] == "Experience"]
    project_units    = [u for u in units if u["section"] == "Projects"]

    hard_skills_str = ", ".join(ats_keywords.hard_skills)
    soft_skills_str = ", ".join(ats_keywords.soft_skills)

    # Concurrency control: 1 request at a time with pacing prevents tripping Groq 8,000 TPM limit
    semaphore = asyncio.Semaphore(1)

    async def run_section(
        input_data: dict,
        raw_units: List[ResumeUnit],
        max_retries: int = 5
    ) -> SectionOutput:
        from resume_customizer.merger import auto_repair_unit_braces, is_braces_balanced
        import re
        sec_name = input_data["section_name"]
        raw_units_map = {u["id"]: u["text"] for u in raw_units}

        async with semaphore:
            # Pacing pause between sequential calls to stay comfortably under Groq 8,000 TPM
            await asyncio.sleep(2.0)
            for attempt in range(max_retries):
                try:
                    res: SectionOutput = await section_chain.ainvoke(input_data)
                    # Auto-heal and validate brace balance for modified units
                    has_unrecoverable_brace_error = False
                    for unit in res.units:
                        if unit.modified and not is_braces_balanced(unit.rewritten):
                            orig_text = raw_units_map.get(unit.id, "")
                            repaired = auto_repair_unit_braces(orig_text, unit.rewritten)
                            if is_braces_balanced(repaired):
                                unit.rewritten = repaired
                            else:
                                has_unrecoverable_brace_error = True
                                break

                    if has_unrecoverable_brace_error:
                        raise ValueError(f"Unbalanced curly braces in {sec_name} output that could not be auto-repaired.")

                    return res
                except Exception as e:
                    err_msg = str(e).lower()
                    if "429" in err_msg or "rate_limit" in err_msg:
                        # Extract exact wait time requested by Groq if available
                        wait_match = re.search(r'try again in (\d+(?:\.\d+)?)\s*s', err_msg)
                        if wait_match:
                            wait_sec = float(wait_match.group(1)) + 1.5
                        else:
                            wait_sec = 10.0 * (attempt + 1)
                        print(f"Rate limit hit for {sec_name}, retrying in {wait_sec:.1f}s (attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(wait_sec)
                    elif "validation" in err_msg or "json" in err_msg or "400" in err_msg or "brace" in err_msg:
                        print(f"Formatting/brace issue for {sec_name}, retrying in 2s (attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(2)
                    else:
                        print(f"Unexpected error in {sec_name}: {e}")
                        break

            print(f"[FALLBACK] Keeping original bullets for {sec_name}.")
            return SectionOutput(
                units=[
                    RewrittenUnit(id=u["id"], rewritten=u["text"], modified=False)
                    for u in raw_units
                ]
            )

    print("Running section rewriting pipeline with request governance...")

    # 1. Summary
    summary_input = {
        "section_name": "Summary",
        "job_description": job_description,
        "hard_skills": hard_skills_str,
        "soft_skills": soft_skills_str,
        "units_to_rewrite": format_units_to_rewrite(summary_units),
    }

    # 2. Technical Skills
    skills_input = {
        "section_name": "Technical Skills",
        "job_description": job_description,
        "hard_skills": hard_skills_str,
        "soft_skills": soft_skills_str,
        "units_to_rewrite": format_units_to_rewrite(skills_units),
    }

    # 3. Projects
    projects_input = {
        "section_name": "Projects",
        "job_description": job_description,
        "hard_skills": hard_skills_str,
        "soft_skills": soft_skills_str,
        "units_to_rewrite": format_units_to_rewrite(project_units),
    }

    # 4. Experience (Sub-batched into chunks of max 4 units to stay within token budgets)
    exp_chunks = chunk_units(experience_units, chunk_size=4)
    exp_tasks = []
    for idx, chunk in enumerate(exp_chunks):
        chunk_input = {
            "section_name": f"Experience (Batch {idx + 1}/{len(exp_chunks)})",
            "job_description": job_description,
            "hard_skills": hard_skills_str,
            "soft_skills": soft_skills_str,
            "units_to_rewrite": format_units_to_rewrite(chunk),
        }
        exp_tasks.append(run_section(chunk_input, chunk))

    # Execute all tasks with controlled concurrency
    summary_task = run_section(summary_input, summary_units)
    skills_task = run_section(skills_input, skills_units)
    projects_task = run_section(projects_input, project_units)

    summary_out, skills_out, proj_out, *exp_chunk_results = await asyncio.gather(
        summary_task,
        skills_task,
        projects_task,
        *exp_tasks
    )

    # Combine sub-batched experience results
    combined_exp_units: List[RewrittenUnit] = []
    for chunk_res in exp_chunk_results:
        combined_exp_units.extend(chunk_res.units)
    exp_out = SectionOutput(units=combined_exp_units)

    return {
        "summary": summary_out,
        "skills": skills_out,
        "experience": exp_out,
        "projects": proj_out,
    }


def rewrite_bullets(
    job_description: str,
    ats_keywords: Optional[ATSKeywords] = None,
    file_name: Path = Path(__file__).resolve().parents[2] / "resume.tex"
) -> Dict[str, SectionOutput]:
    """Synchronous wrapper for arewrite_bullets."""
    return asyncio.run(
        arewrite_bullets(
            job_description=job_description,
            ats_keywords=ats_keywords,
            file_name=file_name
        )
    )


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    sample_jd = (
        "We are looking for a Senior Full Stack Engineer with strong experience in Python, "
        "Next.js, Node.js, and Redis. Experience with async message queues, Docker, and AWS is a huge plus. "
        "Must be a self-starter who excels at cross-functional communication and lead tracking systems."
    )

    sample_keywords = ATSKeywords(
        hard_skills=["Python", "Next.js", "Node.js", "Redis", "Docker", "AWS"],
        soft_skills=["Communication", "Leadership", "Self-starter"],
        seniority_levels=["Senior"]
    )

    results = rewrite_bullets(
        job_description=sample_jd,
        ats_keywords=sample_keywords
    )

    for section_name, output in results.items():
        print(f"\n==================== {section_name.upper()} ====================")
        for unit in output.units:
            status = "[MODIFIED]" if unit.modified else "[UNCHANGED]"
            print(f"{status} {unit.id}:\n  {unit.rewritten}\n")
