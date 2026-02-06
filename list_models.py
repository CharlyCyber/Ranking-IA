import google.generativeai as genai
from src.config import GEMINI_API_KEY

def list_models():
    genai.configure(api_key=GEMINI_API_KEY)
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(m.name)

if __name__ == "__main__":
    from dotenv import load_dotenv
    import os
    load_dotenv()
    list_models()
