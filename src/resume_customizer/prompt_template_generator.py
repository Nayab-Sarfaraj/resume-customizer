from langchain_core.prompts import PromptTemplate
ats_keyword_extractor_prompt = PromptTemplate(
    template="""
    Please identify the ATS keywords in the following job description text:
    {job_description_text}
    """,
    input_variables=["job_description_text"],
    validation=True
)

ats_keyword_extractor_prompt.save("templates/ats_keyword_extractor_template.json")