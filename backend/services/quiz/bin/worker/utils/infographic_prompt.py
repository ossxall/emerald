from utils.opencode_client import opencode_completion

def get_system_prompt()-> str:
    return """You must generate a valid JSON object containing all the provided information without omitting, summarizing, altering, or discarding any details. Preserve the original structure, meaning, and data integrity exactly as provided."""

def _llm(prompt: str, max_tokens: int = 1000) -> str:
    """Generate infographic data with OpenCode."""
    return opencode_completion(get_system_prompt(), prompt, max_tokens)

def create_infographic(prompt: str, max_tokens: int)-> str:
    return _llm(prompt, max_tokens)
