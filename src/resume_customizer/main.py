import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from resume_customizer.utils import read_file
from resume_customizer.parser import extract_units
from resume_customizer.ats import extract_ats_keywords
from resume_customizer.section_chains import rewrite_bullets
from resume_customizer.merger import merge_rewrites, MergeResult


def compile_latex(tex_path: Path, output_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Attempts to compile the .tex file to PDF using tectonic or pdflatex if available.
    Returns the Path to the compiled PDF, or None if no compiler is installed or compilation fails.
    """
    if output_dir is None:
        output_dir = tex_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{tex_path.stem}.pdf"

    # 1. Check for Tectonic
    tectonic_bin = shutil.which("tectonic")
    if tectonic_bin:
        print(f"[COMPILE] Compiling {tex_path.name} with Tectonic...")
        res = subprocess.run(
            [tectonic_bin, str(tex_path), "--outdir", str(output_dir)],
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and pdf_path.exists():
            print(f"[COMPILE SUCCESS] PDF generated at: {pdf_path}")
            return pdf_path
        else:
            print(f"[COMPILE ERROR] Tectonic compilation failed:\n{res.stderr or res.stdout}")
            return None

    # 2. Fallback to pdflatex
    pdflatex_bin = shutil.which("pdflatex")
    if pdflatex_bin:
        print(f"[COMPILE] Compiling {tex_path.name} with pdflatex...")
        res = subprocess.run(
            [pdflatex_bin, f"-output-directory={output_dir}", "-interaction=nonstopmode", str(tex_path)],
            capture_output=True,
            text=True
        )
        if res.returncode == 0 and pdf_path.exists():
            print(f"[COMPILE SUCCESS] PDF generated at: {pdf_path}")
            return pdf_path
        else:
            print(f"[COMPILE ERROR] pdflatex compilation failed:\n{res.stderr or res.stdout}")
            return None

    print(f"\n[COMPILE INFO] Neither 'tectonic' nor 'pdflatex' was found on your system.")
    print(f"               Your optimized LaTeX file is saved at: {tex_path}")
    print(f"               You can compile it online (e.g. Overleaf) or install Tectonic via:")
    print(f"               cargo install tectonic   OR   winget install tectonic")
    return None


def customize_resume(
    job_description: str,
    resume_path: Path = Path(__file__).resolve().parents[2] / "resume.tex",
    output_dir: Path = Path(__file__).resolve().parents[2] / "output",
    compile_pdf: bool = True,
) -> Dict[str, Any]:
    """
    End-to-end orchestration pipeline:
    1. Read original resume.tex
    2. Extract ATS Keywords from the target Job Description
    3. Parse optimizable units and character spans
    4. Run 4 section chains in parallel via asyncio.gather with self-healing retries
    5. Reverse-offset slice and merge with pre- & post-merge validation gates
    6. Compile to PDF if a compiler is available
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 65)
    print("RESUME CUSTOMIZER PIPELINE: JD -> ATS -> LLMs -> MERGE -> COMPILE")
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

    # Step 6: Compile
    pdf_path = None
    if compile_pdf:
        print("\n" + "-" * 65)
        print("COMPILATION")
        print("-" * 65)
        pdf_path = compile_latex(merge_result.output_path, output_dir=output_dir)

    return {
        "ats_keywords": ats_keywords,
        "merge_result": merge_result,
        "pdf_path": pdf_path,
    }


def main() -> None:
    sample_jd = (
        "We are looking for a Senior Full Stack Engineer with strong experience in Python, "
        "Next.js, Node.js, and Redis. Experience with async message queues, Docker, and AWS is a huge plus. "
        "Must be a self-starter who excels at cross-functional communication and lead tracking systems."
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
