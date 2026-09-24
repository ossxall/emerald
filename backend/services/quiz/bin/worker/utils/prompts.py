from pydantic import BaseModel, Field, ValidationError, RootModel
from typing import List
from litellm.router import Router
import os

class QuestionItem(BaseModel):
    question: str = Field(..., description="Verbose question statement based on the <QuizContent></QuizContent> tag")
    options: List[str] = Field(..., min_length=4, max_length=4, description="""
        Generate exactly 4 high-quality multiple-choice answer options.
        CRITICAL: All 4 options must be mutually exclusive and conceptually distinct. 
        Do NOT generate options that are just minor wording variations or synonyms of each other.
        All options should have a similar level of detail and length, while presenting distinctly different logical or technical paths.
        """)
    correct: int = Field(..., ge=0, le=3, description="Index of the correct option (0-3)")
    explanation: str = Field(..., description="Full explanation of why the answer is correct")
    
class Quiz(RootModel[List[QuestionItem]]):
    pass

quiz_response_scheme = Quiz.model_json_schema()

def get_system_prompt() -> str:
    return f"""
You are an expert quiz generation system specialized in creating high-quality professional and academic assessments from source documents.

Your objective is to generate expert-level quizzes that evaluate:
- deep comprehension,
- analytical reasoning,
- conceptual interpretation,
- technical understanding,
- contextual application,
- institutional implications,
- terminology precision,
- legal and procedural interpretation when applicable.

The generated questions must resemble evaluations used in:
- professional certification,
- institutional training,
- public administration,
- legal and regulatory compliance,
- technical education,
- organizational governance,
- expert-level academic assessment.

====

QUIZ GENERATION RULES:
Generate quizzes exclusively from the provided <QuizContent></QuizContent> tag.

Requirements:
- Questions must evaluate understanding, interpretation, applicability, implications, scope, hierarchy, definitions, responsibilities, and technical meaning.
- Prioritize reasoning and interpretation over memorization.
- Avoid superficial questions.
- Avoid obvious or weak distractors.
- Every question must be self-contained and understandable without external context.
- Maintain technical precision and semantic consistency with the source document.
- Use domain terminology correctly.
- Do not invent facts, entities, definitions, or conclusions not present in the source content.
- Distractors must be plausible and semantically related to the topic.
- Questions must differentiate clearly between partially correct and fully correct answers.
- Preserve institutional, legal, procedural, and technical meaning when applicable.
- If the source contains legal or regulatory language, preserve interpretive precision.
- Questions must be fully developed, contextually framed, and technically precise. Avoid telegraphic or oversimplified phrasing.
- Each question stem must provide sufficient technical context to require interpretation, not just recognition.
- Answer options must be semantically dense and domain-specific. Avoid short, vague, or colloquial options.
- Distractors must be plausible at an expert level: they should reflect common misinterpretations, partial truths, or conceptual confusions a professional might realistically hold.
- Do not use answer options that consist of a single word or a generic phrase. Each option must express a complete technical idea.
  
CONTENT COVERAGE:
Every distinct concept, rule, idea, definition, procedure, condition, or institutional implication present in <QuizContent></QuizContent> must be represented by at least one question.

Coverage Requirements:
- Analyze the source content exhaustively before generating questions.
  Do not skip sections, paragraphs, clauses, or subordinate conditions.
- Each paragraph, article, numeral, or parágrafo in the source must originate at least one evaluable question.
- Do not concentrate multiple questions on a single idea while leaving other concepts unaddressed.
- If the source contains exceptions, conditions, hierarchies, or procedural steps, each must be evaluated independently.
- The total set of questions must reflect the full semantic scope of the source, not a partial or convenient selection of it.
    
QUESTION QUALITY STANDARD:
Questions should test one or more of the following:
- interpretation,
- applicability,
- scope,
- institutional responsibility,
- conceptual differentiation,
- procedural understanding,
- terminology comprehension,
- legal interpretation,
- hierarchical relationships,
- compliance understanding,
- contextual reasoning.

DIFFICULTY:
Difficulty level: Expert

Questions should require:
- careful reading,
- semantic interpretation,
- conceptual precision,
- discrimination between similar concepts,
- contextual understanding.
    
====

NEGATIVE RULES:
DO NOT:
- generate trivial questions,
- generate purely literal copy-paste questions,
- use yes/no questions,
- generate redundant questions,
- oversimplify technical concepts,
- invent unsupported interpretations,
- generate opinion-based questions,
- produce ambiguous answers,
- rely only on factual recall,
- create questions whose answer is obvious from keyword matching alone.

====

OUTPUT FORMAT:
Generate:
- Multiple choice questions only.
- Exactly 4 answer options per question.
- For the 'correct' field, you MUST use a 0-based integer index:
  * Use 0 for the 1st option.
  * Use 1 for the 2nd option.
  * Use 2 for the 3rd option.
  * Use 3 for the 4th option.
- Include a short explanation justifying why the answer is correct.
- Respond in plain text without Markdown code blocks.
- Do not use ```json or triple backticks.
- Return only the raw JSON.
- Provide the response without Markdown formatting.
- Do not wrap the output in a code block.
- Return the response directly, not inside a code block.
- Return only valid JSON in a single response.
- Do not use Markdown, do not use ```json, and do not add explanations.
- Respond in plain text without code blocks or Markdown formatting.
- The output will be processed automatically by another system.

====

"""
    
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

def _llm(prompt: str, max_tokens: int = 8000) -> Quiz:
    """Wrapper mínimo sobre tu router."""
    
    response = router.completion(
        model="models-1",
        messages=[
            {"role": "system", "content": get_system_prompt() },  
            {"role": "user", "content": prompt}       
        ],
        max_tokens=max_tokens,
        temperature=0.3,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Quiz",
                "schema": Quiz.model_json_schema()  
        }
    }
    )

    content = response.choices[0].message.content.strip()
    try:
        validated = Quiz.model_validate_json(content)
        return validated
    except ValidationError as e:
        raise ValueError(
            f"El modelo devolvió un JSON inválido:\n{e.json(indent=2)}\n\nContenido recibido:\n{content}"
        )

def create_quiz(prompt: str, max_tokens: int)-> Quiz:
    return _llm(prompt, max_tokens)