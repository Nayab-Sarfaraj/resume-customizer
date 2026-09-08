import os
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

model = ChatGroq(
    model=MODEL_NAME,
    max_tokens=2048,
)
