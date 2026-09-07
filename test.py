import sys
from pathlib import Path

# Add src to sys.path so we can import resume_customizer
sys.path.insert(0, str(Path(__file__).parent / "src"))

from resume_customizer import parse_sections, extract_units


if __name__ == "__main__":
    with open("resume.tex", "r", encoding="utf-8") as f:
        content = f.read()

    sections = parse_sections(content)
    print(f"--- Found {len(sections)} sections ---")
    for s in sections:
        print(f"  * {s['name']} (span: {s['content_span'][0]} -> {s['content_span'][1]})")

    print("\n--- Extracting Units ---")
    units = extract_units(content)
    print(f"Total optimizable units extracted: {len(units)}\n")

    for u in units:
        preview = u['text'][:75] + "..." if len(u['text']) > 75 else u['text']
        print(f"[{u['id']}] ({u['section']} | {u['context']})")
        print(f"  Span: {u['span']}")
        print(f"  Text: {preview}\n")

    print("--- Verifying Spans against resume.tex ---")
    all_matched = True
    for u in units:
        extracted_slice = content[u['span'][0]:u['span'][1]]
        if extracted_slice != u['text']:
            print(f"MISMATCH for {u['id']}!")
            all_matched = False
    if all_matched:
        print("[SUCCESS] All 19 units match their exact character spans in resume.tex!")