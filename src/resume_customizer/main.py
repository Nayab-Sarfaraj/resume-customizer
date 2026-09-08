import sys
from pathlib import Path
from typing import Dict, Any

from resume_customizer.utils import read_file
from resume_customizer.parser import extract_units
from resume_customizer.ats import extract_ats_keywords
from resume_customizer.section_chains import rewrite_bullets
from resume_customizer.merger import merge_rewrites, MergeResult


def customize_resume(
    job_description: str,
    resume_path: Path = Path(__file__).resolve().parents[2] / "resume.tex",
    output_dir: Path = Path(__file__).resolve().parents[2] / "output",
) -> Dict[str, Any]:
    """
    End-to-end orchestration pipeline:
    1. Read original resume.tex
    2. Extract ATS Keywords from the target Job Description
    3. Parse optimizable units and character spans
    4. Run 4 section chains in parallel via asyncio.gather with self-healing retries
    5. Reverse-offset slice and merge with pre- & post-merge validation gates
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    print("=" * 65)
    print("RESUME CUSTOMIZER PIPELINE: JD -> ATS -> LLMs -> MERGE")
    print("=" * 65)

    # Step 1: Read base resume
    print(f"\n[1/5] Reading base resume from: {resume_path}")
    original_latex = read_file(resume_path)

    # Step 2: Extract ATS keywords
    print("\n[2/5] Extracting ATS keywords from Job Description...")
    ats_keywords = extract_ats_keywords(job_description)
    print(f"      * Hard Skills: {', '.join(ats_keywords.hard_skills)}")
    print(f"      * Soft Skills: {', '.join(ats_keywords.soft_skills)}")
    print(f"      * Seniority:   {', '.join(ats_keywords.seniority_levels)}")

    # Step 3: Parse units
    print("\n[3/5] Parsing LaTeX structure and optimizable units...")
    units = extract_units(original_latex)
    print(f"      * Found {len(units)} units across 4 active sections.")

    # Step 4: Run parallel section chains
    print("\n[4/5] Running section rewriting chains in parallel...")
    section_results = rewrite_bullets(
        job_description=job_description,
        ats_keywords=ats_keywords,
        file_name=resume_path
    )

    # Step 5: Merge rewrites with reverse offset
    print("\n[5/5] Merging rewrites with reverse-offset slicing & validation...")
    output_tex = output_dir / "resume_optimized.tex"
    merge_result: MergeResult = merge_rewrites(
        original_latex=original_latex,
        units=units,
        section_results=section_results,
        output_path=output_tex
    )

    print("\n" + "=" * 65)
    print("PIPELINE SUMMARY")
    print("=" * 65)
    print(f"Total Input Units:   {len(units)}")
    print(f"Units Modified:      {merge_result.modified_count}")
    print(f"Units Unchanged:     {merge_result.unchanged_count}")
    print(f"Units Rejected:      {merge_result.rejected_count}")
    if merge_result.rejected_ids:
        print(f"Rejected Unit IDs:   {merge_result.rejected_ids}")
    print(f"Optimized LaTeX:     {merge_result.output_path}")

    return {
        "ats_keywords": ats_keywords,
        "merge_result": merge_result,
    }


def main() -> None:
    sample_jd = (
        """About Us:
At Parsewave we're building advanced datasets that help train the next generation of coding AI systems. Our contributors are engineers, researchers, and developers who create and test problems that challenge models to think and act like real-world programmers
So far this year, we've collaborated with engineers from across the world to craft realistic engineering environments, terminal workflows, and agentic debugging tasks used by leading AI research labs.
About You:
Just a few short questions before we proceed.
Full Name:
*
Nayab Sarfaraj
Email:
*
nayabsarfaraj@gmail.com
Please provide your discord. 
Discord: 
*
nay0961
If you have a detailed Github / Personal Site the resume option becomes optional. 
GitHub link (Even if empty! We need it for future steps)
*
https://github.com/Nayab-Sarfaraj
Personal Portfolio / Site:
https://portfolio-self-chi-m6v05x2bws.vercel.app/
X Profile:
https://x.com/NayabSarfaraj
Which of the following skills are you familiar with? 
*


🐧 Linux, ubuntu, debian

⚙️ Docker, docker-compose

🐍 Python, pip, uv

🖥️ Bash, zsh, sh

🤖 Cursor, Claude code, Codex
We are open to all language types, only familiarity with CLI is a necessity."""
    )

    # Allow custom JD via file argument if provided
    if len(sys.argv) > 1:
        arg_path = Path(sys.argv[1])
        if arg_path.exists():
            print(f"Loading Job Description from file: {arg_path}")
            job_description = arg_path.read_text(encoding="utf-8")
        else:
            job_description = sys.argv[1]
    else:
        print("No job description provided via arguments. Using sample target JD.")
        job_description = sample_jd

    customize_resume(job_description=job_description)


if __name__ == "__main__":
    main()
