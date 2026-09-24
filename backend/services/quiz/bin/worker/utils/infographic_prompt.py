from litellm.router import Router
import os

model_list = [
    {
        "model_name": "models-1",
        "litellm_params": {
            "model": f"gemini/{os.environ.get('QUIZ_GEMINI_MODEL', 'gemini-3.1-flash-lite')}"
        }
    },
    {
        "model_name": "models-1",
        "litellm_params": {
            "model": "ollama/gemma4:e4b",
            "api_base": "http://localhost:11434"
        }
    }
]    

router = Router(model_list=model_list)

def get_system_prompt()-> str:
    return """You must generate a valid JSON object containing all the provided information without omitting, summarizing, altering, or discarding any details. Preserve the original structure, meaning, and data integrity exactly as provided."""

def _llm(prompt: str, max_tokens: int = 1000) -> str:
    """Wrapper mínimo sobre tu router."""
    
    response = router.completion(
        model="models-1",
        messages=[
            { "role": "system", "content": get_system_prompt() }, 
            { "role": "user", "content": prompt }
        ],
        max_tokens=max_tokens,
        temperature=0.3
    )

    content = response.choices[0].message.content.strip()
    return content

def create_infographic(prompt: str, max_tokens: int)-> str:
    return _llm(prompt, max_tokens)