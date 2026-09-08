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
2. Never invent, change, or remove any number, percentage, date, or metric. If the original says "40%", the rewrite must still say "40%" — not a different number, not removed.
3. You may reword phrasing and insert a keyword from the list above ONLY if it is truthful to what the original bullet already claims. Do not claim a tool, skill, or outcome that isn't already implied by the original text.
4. Preserve every LaTeX command exactly as-is — \\textbf{{}}, \\%, $\\sim$, →, and all brace structure. You are rewriting the English content inside commands, not the LaTeX syntax itself.
5. Preserve original spacing/formatting quirks around commands even if they look inconsistent.
6. If a unit has nothing relevant to improve for this job description, return it unchanged with "modified": false rather than forcing a keyword in.
7. For Technical Skills units specifically: only APPEND relevant new items to the existing comma-separated list under its category. Never remove existing items, never restructure into prose.

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


async def arewrite_bullets(
    job_description: str,
    ats_keywords: Optional[ATSKeywords] = None,
    file_name: Path = Path(__file__).resolve().parents[2] / "resume.tex"
) -> Dict[str, SectionOutput]:
    """
    Asynchronously runs section-specific rewriting chains in parallel via asyncio.gather.
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

    # Prepare inputs for each section
    summary_input = {
        "section_name": "Summary",
        "job_description": job_description,
        "hard_skills": hard_skills_str,
        "soft_skills": soft_skills_str,
        "units_to_rewrite": format_units_to_rewrite(summary_units),
    }

    skills_input = {
        "section_name": "Technical Skills",
        "job_description": job_description,
        "hard_skills": hard_skills_str,
        "soft_skills": soft_skills_str,
        "units_to_rewrite": format_units_to_rewrite(skills_units),
    }

    experience_input = {
        "section_name": "Experience",
        "job_description": job_description,
        "hard_skills": hard_skills_str,
        "soft_skills": soft_skills_str,
        "units_to_rewrite": format_units_to_rewrite(experience_units),
    }

    projects_input = {
        "section_name": "Projects",
        "job_description": job_description,
        "hard_skills": hard_skills_str,
        "soft_skills": soft_skills_str,
        "units_to_rewrite": format_units_to_rewrite(project_units),
    }

    semaphore = asyncio.Semaphore(2)

    async def run_section(
        input_data: dict,
        raw_units: List[ResumeUnit],
        max_retries: int = 3
    ) -> SectionOutput:
        sec_name = input_data["section_name"]
        async with semaphore:
            for attempt in range(max_retries):
                try:
                    return await section_chain.ainvoke(input_data)
                except Exception as e:
                    err_msg = str(e).lower()
                    if "429" in err_msg or "rate_limit" in err_msg:
                        wait_sec = 8.0 * (attempt + 1)
                        print(f"Rate limit hit for {sec_name}, retrying in {wait_sec}s (attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(wait_sec)
                    elif "validation" in err_msg or "json" in err_msg or "400" in err_msg:
                        print(f"JSON/formatting issue for {sec_name}, retrying in 2s (attempt {attempt + 1}/{max_retries})...")
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

    print("Running 4 section chains in parallel...")
    summary_out, skills_out, exp_out, proj_out = await asyncio.gather(
        run_section(summary_input, summary_units),
        run_section(skills_input, skills_units),
        run_section(experience_input, experience_units),
        run_section(projects_input, project_units),
    )

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
