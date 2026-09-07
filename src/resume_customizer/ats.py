import json
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import PromptTemplate

load_dotenv()

model = ChatGroq(
    model="openai/gpt-oss-20b"
)


class ATSKeywords(BaseModel):
    hard_skills: list[str] = Field(
        default_factory=list,
        description="List of hard skills keywords that ATS might look for in a resume"
    )

    soft_skills: list[str] = Field(
        default_factory=list,
        description="List of soft skills keywords that ATS might look for in a resume"
    )

    seniority_levels: list[str] = Field(
        default_factory=list,
        description="List of seniority levels keywords that ATS might look for in a resume"
    )


structured_model = model.with_structured_output(ATSKeywords)


def load_template() -> PromptTemplate:
    # Resolve template path: check relative to this file first, fallback to root templates/
    template_path = Path(__file__).parent / "templates" / "ats_keyword_extractor_template.json"
    if not template_path.exists():
        template_path = Path("templates/ats_keyword_extractor_template.json")

    with open(template_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "kwargs" in data and "template" in data["kwargs"]:
        template_text = data["kwargs"]["template"]
        input_vars = data["kwargs"].get("input_variables", ["job_description_text"])
    else:
        template_text = data.get("template", "")
        input_vars = data.get("input_variables", ["job_description_text"])

    return PromptTemplate(template=template_text, input_variables=input_vars)


def extract_ats_keywords(job_description_text: str) -> ATSKeywords:
    template = load_template()
    prompt = template.invoke({
        "job_description_text": job_description_text
    })

    messages = [
        SystemMessage(
            content="You are a helpful assistant that extracts ATS keywords from job descriptions."
        ),
        HumanMessage(content=prompt.to_string())
    ]

    response = structured_model.invoke(messages)
    print(f"Extracted ATS Keywords: {response}")
    return response


if __name__ == "__main__":
    extract_ats_keywords(
        job_description_text="""We are looking for a Senior Software Engineer with experience in Python, JavaScript, and cloud technologies. The ideal candidate should have strong problem-solving skills, excellent communication abilities, and a proven track record of leading development teams."""
    )