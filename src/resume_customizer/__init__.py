from .parser import (
    ResumeSection,
    ResumeUnit,
    extract_braced_content,
    is_line_commented,
    strip_with_offsets,
    parse_sections,
    extract_units,
)
from .ats import (
    ATSKeywords,
    extract_ats_keywords,
)

__all__ = [
    "ResumeSection",
    "ResumeUnit",
    "extract_braced_content",
    "is_line_commented",
    "strip_with_offsets",
    "parse_sections",
    "extract_units",
    "ATSKeywords",
    "extract_ats_keywords",
    "main",
]


def main() -> None:
    print("Hello from resume-customizer!")
