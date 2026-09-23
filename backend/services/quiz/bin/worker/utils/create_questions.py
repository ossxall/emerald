import json
import logging
from utils.clean_json import limpiar_json_markdown
from pydantic import ValidationError
from prompts.question_prompts import PromptConfig, get_create_questions_prompts
from clients.gemini_client import GeminiChat
from clients.openai_client import OpenAIChat
from clients.ollama_client import OllamaChat
from tenacity import retry, wait_fixed, stop_after_attempt
import os
import glob
import re

logger = logging.getLogger("rich")

SYSTEM_PROMPT = """
Eres un asistente experto en normativa colombia vigente año 2026
"""


chat = OllamaChat(
    model='gemma3:4b',
    system_prompt=SYSTEM_PROMPT
)

chat = GeminiChat(
    model="gemini-3.1-flash-lite",
    system_prompt=SYSTEM_PROMPT,
)

  
def get_article_files() -> list[dict]:
    """
    Lee ordenadamente todos los archivos .md de un directorio,
    ordenados por el número antes del .md
    """
    patron = os.path.join("output/articles", "*.md")
    archivos = sorted(
        glob.glob(patron),
        key=lambda x: int(re.search(r'(\d+)(?=\.md$)', x).group())
    )

    resultados = []

    for ruta in archivos:
        nombre = os.path.basename(ruta)

        with open(ruta, "r", encoding="utf-8") as f:
            contenido = f.read()

        resultados.append({
            "nombre": nombre,
            "ruta": ruta,
            "contenido": contenido
        })

    print(f"[DEBUG] Total de archivos leídos: {len(resultados)}")
    return resultados

def extraer_contexto(n: int = 3) -> str:
    """
    Reads the first n .md articles from the output directory and
    combines them into a single string.

    Args:
        n: Number of articles to include (default: 3)

    Returns:
        A string containing the combined content of the first n articles
    """
    # Retrieve all available article files
    resultados = get_article_files()
    
    # Select the first n articles
    articles_files = resultados[:n]
    
    # Initialize the context string
    contexto = ""
    
    # Iterate over the selected articles
    for file in articles_files:
        print(f"[DEBUG] Agregando al contexto: {file['nombre']}")
        contexto += f"\n\n--- {file['nombre']} ---\n\n"
        contexto += file["contenido"]
    
    print(f"[DEBUG] Contexto generado con {len(articles_files)} artículos ({len(contexto)} caracteres)")
    return contexto   


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def save_json_file(output_path: str, filename: str, data: str):
    """
    Saves a JSON string as a JSON file in 'output/questions'.
    """
    try:
        output_path
        os.makedirs(output_path, exist_ok=True)
        file_path = os.path.join(output_path, filename)
        
        # If data is a string, parse it into a Python object
        if isinstance(data, str):
            data = json.loads(data)
        
        # Write JSON to file
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"JSON file saved at: {file_path}")
        return file_path

    except Exception as e:
        logger.error(f"Failed to save JSON (attempt failed): {e}")
        raise  

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def save_md_file(output_path: str, filename: str, data: str):
    """
    Saves a markdown string as a .md file in the specified output path.
    """
    try:
        os.makedirs(output_path, exist_ok=True)
        file_path = os.path.join(output_path, filename)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(data)
        
        logger.info(f"Markdown file saved at: {file_path}")
        return file_path

    except Exception as e:
        logger.error(f"Failed to save markdown (attempt failed): {e}")
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def try_prompt(prompt: PromptConfig):
    """
    Executes a single AI prompt with automated retries, manual debug intervention,
    JSON cleaning, and Pydantic validation.

    Args:
        prompt (PromptConfig): Configuration object containing the content, 
                               output paths, and validation adapters.

    Returns:
        str: The cleaned and validated response from the AI.

    Raises:
        ValidationError: If the response fails Pydantic schema validation.
        Exception: For unexpected API or runtime errors.
    """
    # Use an infinite loop to allow manual 'r' (retry) triggers in debug mode
    while True:
        try:
            # 1. AI Interaction: Call the chat service
            response = chat.preguntar(prompt.content, prompt.append)
            
            # 2. Sanitization: Clean markdown formatting/JSON blocks
            sanitized_response = limpiar_json_markdown(response)
            
            # Guard clause: Ensure the response isn't empty
            if not sanitized_response or not sanitized_response.strip():
                logger.info("[red]⚠️ Empty IA response[/red]")
                if not prompt.debug: raise ValueError("Respuesta vacía")
                # Si es debug, permitiremos que el usuario decida abajo

            # 3. Human-in-the-loop (Debug Mode)
            if prompt.debug:
                opcion = input("\n[Enter] Continue | [r] Retry: ").strip().lower()
            
                if opcion == 'r':
                    logger.info("🔄 Refreshing response...")
                    continue # Restart the 'while' loop to call the AI again
                else:
                    logger.info("➡️ Proceeding to validation...")

            # 4. Technical Pydantic Validation
            if prompt.type_adapter:
                # Triggers ValidationError if the JSON doesn't match the expected schema
                prompt.type_adapter.validate_json(sanitized_response) 
                logger.info("[green]✅ Pydantic validation successful.[/green]")
                
            # 5. Persistence: Save output based on specified format
            if prompt.save_output:
                match prompt.output_format:
                    case ".json":
                        save_json_file(prompt.output_path, f"{prompt.filename}{prompt.output_format}", sanitized_response)
                    case ".md":
                        save_md_file(prompt.output_path, f"{prompt.filename}{prompt.output_format}", sanitized_response)
                    case _:
                        logger.warning(f"Unsupported output format: {prompt.output_format}")
            
            return sanitized_response

        except ValidationError as e:
            logger.error(f"❌ Validation error in prompt {prompt.index}")

            if prompt.debug:
                logger.info(f"Details: {e.json()}")
                input("Press Enter to retry the AI call manually...")
                continue # Retry inside the 'while' loop
            raise # Let @retry handle it if not in debug mode

        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            raise # Trigger @retry for transient API issues

    
    
def process_articles():
    # 1. Recuperar la lista ya ordenada numéricamente
    article_files = get_article_files()
    
    if not article_files:
        print("[ERROR] No se encontraron archivos para procesar.")
        return

    # 2. Preguntar el punto de inicio
    print(f"\nSe han encontrado {len(article_files)} archivos.")
    inicio = input("¿Desde qué número o nombre quieres empezar? (Enter para inicio): ").strip()
    
    # 3. Extraer contexto (los 3 primeros según tu lógica original)
    context = extraer_contexto(n=3)
    
    # Flag para saber si ya llegamos al punto de inicio
    skip = True if inicio else False

    for file in article_files:
        nombre_actual = file["nombre"]
        
        # Lógica de salto: buscamos coincidencia con el número o el nombre
        if skip:
            if inicio.lower() in nombre_actual.lower():
                skip = False  # ¡Lo encontramos! A partir de aquí procesamos
            else:
                continue # Sigue buscando el archivo inicial

        # --- Flujo de procesamiento ---
        tecla = input(f"\n¿Procesar '{nombre_actual}'? [y/n]: ").strip().lower()
        if tecla == "x":
            print(f"[DEBUG] Task terminated")
            break
        
        if tecla != "y":
            print(f"[DEBUG] Saltando: {nombre_actual}")
            continue
               
        print(f"[DEBUG] Procesando: {nombre_actual}")
        
        filename = os.path.splitext(nombre_actual)[0]
        contenido = file["contenido"]
        
        prompts = get_create_questions_prompts(context, contenido, filename)
        
        if not prompts:
            print(f"[WARN] Sin prompts para {nombre_actual}. Continuando con el siguiente...")
            continue 
        
        chat.limpiar()
        
        for i, prompt in enumerate(prompts):
            logger.info(f"\n[magenta]{'=' * 50}[/magenta]\n[magenta]--- RUNNING PROMPT {i} ---[/magenta]\n[magenta]{'=' * 50}[/magenta]")
            try_prompt(prompt)
        