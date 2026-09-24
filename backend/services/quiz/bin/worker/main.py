
from dotenv import load_dotenv
load_dotenv()

from pathlib import Path
from utils.prompts import Quiz, create_quiz
from utils.extract_multiselect import extract_multiselect
from utils.extract_text import convert_yjs_to_markdown
from utils.extract_keywords import extract_keywords
from utils.generate_context import summarize_to_three_paragraphs
from utils.infographic_prompt import create_infographic
from langdetect import detect
from botocore.config import Config

import re
import asyncio
import json
import os
import sys
import socket
import langcodes
import aioboto3
import xml.etree.ElementTree as ET

OUTPUT_PATH = Path("tmp/output")
OUTPUT_PATH.mkdir(parents=True, exist_ok=True)


def resolve_s3_endpoint() -> str:
    """Elige un endpoint S3 alcanzable.

    Dentro de Docker el hostname `emerald-seaweedfs` resuelve; desde el host
    NO (aunque el puerto esté publicado en localhost). Si el hostname del
    endpoint configurado no resuelve, usamos el endpoint "host" (o el puerto
    publicado por defecto en localhost).
    """
    from urllib.parse import urlparse

    endpoint = os.environ.get("S3_ENDPOINT")
    host_local = os.environ.get("S3_ENDPOINT_HOST", "http://localhost:18333")

    try:
        hostname = urlparse(endpoint).hostname
        socket.getaddrinfo(hostname, None)
        return endpoint
    except (socket.gaierror, OSError):
        print(
            f"WARNING: S3_ENDPOINT={endpoint} no resuelve aquí. "
            f"Usando endpoint local S3_ENDPOINT_HOST={host_local}"
        )
        return host_local


async def download_s3(s3, doc_id: str) -> bytes:
    try:
        response = await s3.get_object(
            Bucket="documents",
            Key=f"{doc_id}/{doc_id}.yjs"
        )

        data = await response["Body"].read()
        return data

    except s3.exceptions.NoSuchKey:
        raise ValueError(
            f"Original YJS binary not found for doc_id={doc_id}"
        )


def replace_xml_content(file_path, tag, new_text):
    if not new_text:
        return
    
    file_path = Path(file_path)

    if not file_path.exists():
        file_path.write_text("", encoding="utf-8")

    content = file_path.read_text(encoding="utf-8")

    pattern = rf"<{tag}>.*?</{tag}>"

    new_block = f"<{tag}>{new_text}</{tag}>"

    if re.search(pattern, content, flags=re.DOTALL):
        # reemplaza si existe
        content = re.sub(pattern, new_block, content, flags=re.DOTALL)
    else:
        # si no existe, lo agrega
        content += new_block + "\n"

    file_path.write_text(content, encoding="utf-8")

    
async def build_context(s3, doc_id: str, bypass_summary: bool):
    print("Downloading S3 binary...")
    data = await download_s3(s3, doc_id)

    print("Converting YJS to markdown...")
    md = convert_yjs_to_markdown(data)

    print("Extracting keywords...")
    keywords = extract_keywords(md)

    detected_language = detect(keywords)
    language = langcodes.Language.get(
        detected_language
    ).display_name()

    print("Generating summary...")
    context = await summarize_to_three_paragraphs(
        md,
        language,
        verbose=True,
        bypass=bypass_summary
    )

    print("Extracting multiselects...")
    multiselects = extract_multiselect(data)
    if not multiselects:
        raise ValueError(
            f"No multiSelect selections found in doc_id={doc_id}. "
            "QuizContent would be empty; aborting instead of reusing a stale context."
        )

    context_path = OUTPUT_PATH / "context.xml"

    replace_xml_content(
        context_path,
        "LanguageRule",
        language
    )
    replace_xml_content(
        context_path,
        "GeneralContext",
        context
    )
    replace_xml_content(
        context_path,
        "GeneralContextKeywords",
        keywords
    )
    replace_xml_content(
        context_path,
        "QuizContent",
        multiselects
    )

    print(f"Context saved at: {context_path}")
    print(
        f"QuizContent ({len(multiselects)} chars): {multiselects[:160]!r}"
    )


def generate_quiz():
    context_path = OUTPUT_PATH / "context.xml"
    if not context_path.exists():
        raise FileNotFoundError(
            "context.xml no existe. Ejecuta build context primero."
        )
    with open(context_path, "r", encoding="utf-8") as f:
        context = f.read()
        
    #-----------------------------------------------------------
         
    print("Generating quiz...")
    result: Quiz = create_quiz(context, 13_000)
    questions_path = OUTPUT_PATH / "questions.json"
    new_questions = result.model_dump()
    if questions_path.exists():
        with open(questions_path, "r", encoding="utf-8") as f:
            existing_questions = json.load(f)

        existing_questions.extend(new_questions)

    else:
        existing_questions = new_questions
    with open(questions_path, "w", encoding="utf-8") as f:
        json.dump(
            existing_questions,
            f,
            ensure_ascii=False,
            indent=2
        )
    print(f"Quiz guardado (append a questions.json): {questions_path}")
    
    #-----------------------------------------------------------
    
    pattern = r"<QuizContent>(.*?)</QuizContent>"
    match = re.search(pattern, context, re.DOTALL)
    if match:
        quiz_content = match.group(1).strip()
    infog = create_infographic(quiz_content, 13_000)
    print("Crea una infografía perfecta con esto:")
    print(infog)
    
    #-----------------------------------------------------------


async def main():
    session = aioboto3.Session()

    config = Config(
        retries={
            "max_attempts": 3,
            "mode": "standard"
        },
        max_pool_connections=12,
        connect_timeout=5,
        read_timeout=30,
    )

    # El documento a procesar debe pasarse explícitamente (argv > env > por defecto)
    # para que /quiz procese SIEMPRE el documento/ selección vigente.
    doc_id = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("QUIZ_DOC_ID", "01a0d05c-99c7-703e-8a43-68424125214a")
    )

    s3_endpoint = resolve_s3_endpoint()

    async with session.client(
        "s3",
        endpoint_url=s3_endpoint,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        region_name=os.environ.get("S3_REGION"),
        config=config
    ) as s3:

        await build_context(s3, doc_id, True)

    generate_quiz()


if __name__ == "__main__":
    asyncio.run(main())