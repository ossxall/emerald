
from utils.convert_to_json import convertir_a_json_formateado
from pydantic import BaseModel, Field, TypeAdapter
from typing import List, Any

class QuestionItem(BaseModel):
    question: str = Field(..., description="Enunciado de la pregunta tipo caso con mención de artículo y normativa")
    options: List[str] = Field(..., min_items=4, max_items=4, description="Lista de opciones posibles")
    correct: int = Field(..., ge=0, le=3, description="Índice de la opción correcta")
    explanation: str = Field(..., description="Explicación completa de la respuesta")

questionsAdapter = TypeAdapter(List[QuestionItem])

formato_preguntas = questionsAdapter.json_schema()


class Prompt(BaseModel):
    index: int
    debug: bool
    append: bool
    save_output: bool
    output_path: str
    output_format: str
    filename: str
    # Usamos Any porque TypeAdapter es un objeto de clase complejo de Pydantic
    type_adapter: Any 
    content: str
    
def get_create_questions_prompts(context: str, article: str, filename: str) -> list[Prompt]:
    p1_data = {
        "index": 0,
        "debug": False,
        "append": True,
        "save_output": False,
        "output_path": "",
        "output_format": "",
        "filename": filename,
        "type_adapter": None,
        
        "content": convertir_a_json_formateado({
            "tarea": "Analizar esta normativa colombiana de 2026.",
            "contexto": context,
            "instrucciones": [
                "Analizar toda la normativa proporcionada.",
                "Usar lenguaje formal académico."
                "Señala si la normativa fue modificada."
            ],
            "normativa": article
        })
    }
    
    p2_data = {
        "index": 1,
        "debug": False,
        "append": True,
        "save_output": True,
        "output_path": "output/lists",
        "output_format": ".md",
        "filename": filename,        
        "type_adapter": None,
        
        "content": convertir_a_json_formateado({
            "tarea": "Verificar tu análisis y hacer una lista numerada sin excluir información.",
            "instrucciones": [
                "Crear una lista enumerada con el resultado del análisis.",
                "La lista numerada no debe tener subnumeración.",
                "Usar este formato: (Número de numeración. Nombre del concepto o idea: contenido)."
            ],
            "normativa": article
        })
        
    }

    p3_data = {
        "index": 2,
        "debug": False,
        "append": False,
        "save_output": True,
        "output_path": "output/questions",
        "output_format": ".json",
        "filename": filename,        
        "type_adapter": questionsAdapter,
                
        "content": convertir_a_json_formateado({
            "tarea": "Crear preguntas de opción múltiple con respuesta única.",
            "instrucciones": [
                "Las preguntas deben ser tipo caso, relacionadas estrictamente con el artículo y cada uno de sus incisos.",
                "Recordar al lector el número del artículo y el nombre exacto de la norma.",
                "Al mencionar un artículo legal, incluir su título o nombre oficial completo en la pregunta, no solo el número.",
                "Aportar más información relevante sobre el artículo legal mencionado en la pregunta para contextualizarlo.",
                "El número de preguntas debe abarcar todo el contexto y contenido del artículo y todos sus incisos.",
                "No dejar ningún tema del artículo sin pregunta."
                "Formatea las preguntas con formato_de_pregunta_json estricto sin excepciones"
            ],
            "formato_de_pregunta_json": formato_preguntas,
            "normativa": article
        })
    }
    
    prompts = [
        Prompt(**p1_data),
        Prompt(**p2_data),
        Prompt(**p3_data)  
    ]

    return prompts