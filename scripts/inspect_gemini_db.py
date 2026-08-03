# scripts/inspect_gemini_db.py
"""
Inspeção dos nomes e tipos exatos de propriedades da database 'Painel de Mercado Diario - Gemini' (3efd828b-84a7-4966-8bdf-fe9c93657edd)
"""
import os
import requests

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
DB_ID = "3efd828b-84a7-4966-8bdf-fe9c93657edd"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def main():
    url = f"https://api.notion.com/v1/databases/{DB_ID}"
    res = requests.get(url, headers=HEADERS)
    print(f"Status: {res.status_code}")
    if res.status_code == 200:
        props = res.json().get("properties", {})
        print("Propriedades na Database Notion do Gemini:")
        for name, data in props.items():
            print(f"  - [{name}] -> Type: {data.get('type')}")
    else:
        print(f"Error: {res.text}")

if __name__ == "__main__":
    main()
