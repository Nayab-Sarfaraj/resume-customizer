import re
from typing import List, Dict, Tuple, Optional, TypedDict


class ResumeSection(TypedDict):
    name: str
    header_span: Tuple[int, int]
    content_span: Tuple[int, int]
    content: str


class ResumeUnit(TypedDict):
    id: str
    section: str
    context: str
    text: str
    span: Tuple[int, int]


def extract_braced_content(text: str, start_pos: int) -> Optional[Tuple[str, int, int]]:
    """
    Finds the first '{' at or after start_pos and extracts the matching balanced content.
    Respects escaped braces ('\\{', '\\}') and comments ('%' not preceded by '\\').
    Returns (content, inner_start_idx, inner_end_idx) or None if not found/unbalanced.
    """
    open_pos = text.find('{', start_pos)
    if open_pos == -1:
        return None

    depth = 1
    i = open_pos + 1
    in_comment = False

    while i < len(text):
        char = text[i]

        if in_comment:
            if char == '\n':
                in_comment = False
            i += 1
            continue

        # Check for escaped characters
        if char == '\\':
            i += 2
            continue

        # Check for start of comment (unescaped %)
        if char == '%':
            in_comment = True
            i += 1
            continue

        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : i], open_pos + 1, i

        i += 1

    return None


def is_line_commented(text: str, pos: int) -> bool:
    """Checks if the character at pos is preceded by an unescaped '%' on the same line."""
    line_start = text.rfind('\n', 0, pos)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1
    
    line_prefix = text[line_start:pos]
    escaped = False
    for ch in line_prefix:
        if escaped:
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == '%':
            return True
    return False


def strip_with_offsets(text: str, start: int, end: int) -> Tuple[str, int, int]:
    """Returns stripped text and adjusts the start and end offsets accordingly."""
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return "", start, start
    leading_ws = len(raw) - len(raw.lstrip())
    trailing_ws = len(raw) - len(raw.rstrip())
    return stripped, start + leading_ws, end - trailing_ws


def parse_sections(latex_text: str) -> List[ResumeSection]:
    """
    Splits the latex document into sections bounded by \\section{...} and \\end{document}.
    Keeps preamble and postamble separate.
    """
    section_pattern = re.compile(r'\\section\{([^}]+)\}')
    end_doc_match = re.search(r'\\end\{document\}', latex_text)
    end_doc_pos = end_doc_match.start() if end_doc_match else len(latex_text)

    matches = list(section_pattern.finditer(latex_text))
    sections: List[ResumeSection] = []

    for i, match in enumerate(matches):
        sec_name = match.group(1).strip()
        sec_header_start = match.start()
        sec_content_start = match.end()

        if i + 1 < len(matches):
            sec_end = matches[i + 1].start()
        else:
            sec_end = end_doc_pos

        sec_raw_text = latex_text[sec_content_start:sec_end]
        sections.append({
            "name": sec_name,
            "header_span": (sec_header_start, sec_content_start),
            "content_span": (sec_content_start, sec_end),
            "content": sec_raw_text
        })

    return sections


def extract_units(latex_text: str) -> List[ResumeUnit]:
    """
    Extracts all optimizable units across sections:
    - Summary
    - Technical Skills
    - Experience
    - Projects
    - Education (skipped if 0 units)
    """
    sections = parse_sections(latex_text)
    units: List[ResumeUnit] = []

    for sec in sections:
        sec_name = sec["name"]
        sec_content = sec["content"]
        sec_start = sec["content_span"][0]

        if sec_name == "Summary":
            item_pattern = re.compile(r'\\resumeItem(?![A-Za-z])\s*\{')
            for m in item_pattern.finditer(sec_content):
                abs_pos = sec_start + m.start()
                if not is_line_commented(latex_text, abs_pos):
                    res = extract_braced_content(sec_content, m.end() - 1)
                    if res:
                        inner_text, s, e = res
                        clean_text, adj_s, adj_e = strip_with_offsets(sec_content, s, e)
                        units.append({
                            "id": f"SUMMARY-{len([u for u in units if u['section'] == 'Summary'])}",
                            "section": sec_name,
                            "context": "Professional Summary",
                            "text": clean_text,
                            "span": (sec_start + adj_s, sec_start + adj_e)
                        })

        elif sec_name == "Technical Skills":
            item_pattern = re.compile(r'\\item(?![A-Za-z])\s*\{')
            for m in item_pattern.finditer(sec_content):
                abs_pos = sec_start + m.start()
                if not is_line_commented(latex_text, abs_pos):
                    res = extract_braced_content(sec_content, m.end() - 1)
                    if res:
                        inner_text, inner_start, inner_end = res
                        raw_lines = inner_text.split(r"\\")
                        curr_offset = 0
                        skill_count = 0
                        for line in raw_lines:
                            line_stripped = line.strip()
                            if line_stripped:
                                line_start_in_inner = inner_text.find(line_stripped, curr_offset)
                                line_end_in_inner = line_start_in_inner + len(line_stripped)
                                curr_offset = line_end_in_inner
                                
                                abs_line_start = sec_start + inner_start + line_start_in_inner
                                abs_line_end = sec_start + inner_start + line_end_in_inner

                                cat_match = re.search(r'\\textbf\{([^}]+)\}', line_stripped)
                                category = cat_match.group(1) if cat_match else f"Skill-{skill_count}"

                                units.append({
                                    "id": f"SKILL-{skill_count}",
                                    "section": sec_name,
                                    "context": category,
                                    "text": line_stripped,
                                    "span": (abs_line_start, abs_line_end)
                                })
                                skill_count += 1
                        break

        elif sec_name == "Experience":
            subheading_pattern = re.compile(r'\\resumeSubheading')
            matches = list(subheading_pattern.finditer(sec_content))
            
            valid_subheadings = []
            for m in matches:
                abs_pos = sec_start + m.start()
                if not is_line_commented(latex_text, abs_pos):
                    args = []
                    curr_p = m.end()
                    for _ in range(4):
                        arg_res = extract_braced_content(sec_content, curr_p)
                        if arg_res:
                            args.append(arg_res[0].strip())
                            curr_p = arg_res[2]
                        else:
                            break
                    company_role = args[0] if len(args) > 0 else "Unknown"
                    valid_subheadings.append((m.start(), company_role))

            item_pattern = re.compile(r'\\resumeItem(?![A-Za-z])\s*\{')

            for block_idx, (block_start_pos, comp_role) in enumerate(valid_subheadings):
                if block_idx + 1 < len(valid_subheadings):
                    block_end_pos = valid_subheadings[block_idx + 1][0]
                else:
                    block_end_pos = len(sec_content)

                block_text = sec_content[block_start_pos:block_end_pos]
                block_abs_start = sec_start + block_start_pos

                bullet_idx = 0
                for m in item_pattern.finditer(block_text):
                    abs_pos = block_abs_start + m.start()
                    if not is_line_commented(latex_text, abs_pos):
                        res = extract_braced_content(block_text, m.end() - 1)
                        if res:
                            inner_text, s, e = res
                            clean_text, adj_s, adj_e = strip_with_offsets(block_text, s, e)
                            units.append({
                                "id": f"EXP-{block_idx}-{bullet_idx}",
                                "section": sec_name,
                                "context": comp_role,
                                "text": clean_text,
                                "span": (block_abs_start + adj_s, block_abs_start + adj_e)
                            })
                            bullet_idx += 1

        elif sec_name == "Projects":
            proj_heading_pattern = re.compile(r'\\resumeProjectHeading')
            matches = list(proj_heading_pattern.finditer(sec_content))

            valid_projects = []
            for m in matches:
                abs_pos = sec_start + m.start()
                if not is_line_commented(latex_text, abs_pos):
                    args = []
                    curr_p = m.end()
                    for _ in range(2):
                        arg_res = extract_braced_content(sec_content, curr_p)
                        if arg_res:
                            args.append(arg_res[0].strip())
                            curr_p = arg_res[2]
                        else:
                            break
                    proj_title = args[0] if len(args) > 0 else "Project"
                    valid_projects.append((m.start(), proj_title))

            item_pattern = re.compile(r'\\resumeItem(?![A-Za-z])\s*\{')

            for proj_idx, (p_start_pos, proj_title) in enumerate(valid_projects):
                if proj_idx + 1 < len(valid_projects):
                    p_end_pos = valid_projects[proj_idx + 1][0]
                else:
                    p_end_pos = len(sec_content)

                block_text = sec_content[p_start_pos:p_end_pos]
                block_abs_start = sec_start + p_start_pos

                bullet_idx = 0
                for m in item_pattern.finditer(block_text):
                    abs_pos = block_abs_start + m.start()
                    if not is_line_commented(latex_text, abs_pos):
                        res = extract_braced_content(block_text, m.end() - 1)
                        if res:
                            inner_text, s, e = res
                            clean_text, adj_s, adj_e = strip_with_offsets(block_text, s, e)
                            units.append({
                                "id": f"PROJ-{proj_idx}-{bullet_idx}",
                                "section": sec_name,
                                "context": proj_title,
                                "text": clean_text,
                                "span": (block_abs_start + adj_s, block_abs_start + adj_e)
                            })
                            bullet_idx += 1

        elif sec_name == "Education":
            pass

    return units
