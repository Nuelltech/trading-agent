# backend/app/services/notion_page_reader_service.py
"""
Módulo: notion_page_reader_service.py (Ferramenta de Leitura de Conteúdo de Páginas Notion)
Finalidade: Extração do texto completo de páginas Notion (ex: Mapas de Transmissão)
Utiliza a API oficial do Notion (GET /v1/blocks/{block_id}/children) com suporte a paginação e recursão.
ESTRITAMENTE LEITURA. NUNCA ESCREVE.
"""

import os
import logging
from typing import Dict, Any, List, Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def _extract_text_from_rich_text(rich_text_list: List[Dict[str, Any]]) -> str:
    """Extrai e concatena plain_text de uma lista rich_text do Notion."""
    if not rich_text_list:
        return ""
    parts = []
    for item in rich_text_list:
        text_content = item.get("plain_text") or item.get("text", {}).get("content", "")
        if text_content:
            parts.append(text_content)
    return "".join(parts)

def fetch_notion_block_children(block_id: str) -> List[Dict[str, Any]]:
    """Obtém todos os blocos filhos de um block_id no Notion com suporte a paginação."""
    if not NOTION_TOKEN:
        logging.error("❌ NOTION_TOKEN não configurado para leitura de blocos.")
        return []

    url = f"https://api.notion.com/v1/blocks/{block_id}/children"
    all_blocks = []
    start_cursor = None

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor

        try:
            res = requests.get(url, headers=NOTION_HEADERS, params=params, timeout=15)
            if res.status_code != 200:
                logging.error(f"❌ Erro HTTP {res.status_code} ao ler blocos de {block_id}: {res.text}")
                break

            data = res.json()
            blocks = data.get("results", [])
            all_blocks.extend(blocks)

            if data.get("has_more") and data.get("next_cursor"):
                start_cursor = data["next_cursor"]
            else:
                break
        except Exception as e:
            logging.error(f"❌ Exceção ao ler blocos do Notion ({block_id}): {e}")
            break

    return all_blocks

def parse_blocks_to_markdown(blocks: List[Dict[str, Any]], depth: int = 0) -> str:
    """Converte uma lista de blocos do Notion numa representação limpa em Markdown."""
    lines = []

    for block in blocks:
        block_type = block.get("type")
        has_children = block.get("has_children", False)
        block_id = block.get("id")

        indent = "  " * depth

        if block_type == "paragraph":
            rt = block.get("paragraph", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"{indent}{text}")

        elif block_type == "heading_1":
            rt = block.get("heading_1", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"\n# {text}")

        elif block_type == "heading_2":
            rt = block.get("heading_2", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"\n## {text}")

        elif block_type == "heading_3":
            rt = block.get("heading_3", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"\n### {text}")

        elif block_type == "bulleted_list_item":
            rt = block.get("bulleted_list_item", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"{indent}- {text}")

        elif block_type == "numbered_list_item":
            rt = block.get("numbered_list_item", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"{indent}1. {text}")

        elif block_type == "toggle":
            rt = block.get("toggle", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"{indent}► {text}")

        elif block_type == "callout":
            rt = block.get("callout", {}).get("rich_text", [])
            icon = block.get("callout", {}).get("icon", {}).get("emoji", "💡")
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"{indent}> {icon} {text}")

        elif block_type == "quote":
            rt = block.get("quote", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            if text:
                lines.append(f"{indent}> {text}")

        elif block_type == "code":
            rt = block.get("code", {}).get("rich_text", [])
            text = _extract_text_from_rich_text(rt)
            lang = block.get("code", {}).get("language", "")
            if text:
                lines.append(f"```{lang}\n{text}\n```")

        # Recursão para blocos filhos (ex: toggles, listas aninhadas)
        if has_children and block_id and depth < 3:
            child_blocks = fetch_notion_block_children(block_id)
            if child_blocks:
                child_md = parse_blocks_to_markdown(child_blocks, depth + 1)
                if child_md:
                    lines.append(child_md)

    return "\n".join(lines)

def fetch_notion_page_content(page_id: str) -> str:
    """
    Função Principal: Lê todos os blocos de uma página Notion (por page_id)
    e devolve o texto completo formatado em Markdown.
    """
    clean_id = page_id.replace("-", "").strip()
    logging.info(f"📖 Lendo texto completo da página Notion ID '{clean_id}'...")
    
    blocks = fetch_notion_block_children(clean_id)
    if not blocks:
        logging.warning(f"⚠️ Nenhum bloco encontrado para a página Notion ID '{clean_id}'.")
        return ""

    markdown_text = parse_blocks_to_markdown(blocks)
    logging.info(f"✅ Texto da página Notion carregado com sucesso ({len(markdown_text)} caracteres).")
    return markdown_text
