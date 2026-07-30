# scripts/inspect_claude_databases.py
"""
Script de Inspeção Dinâmica dos Schemas Notion para as databases #3 e #4
"""
import os
import requests
import json

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CLAUDE_CLOSE_DATABASE_ID = "25fd82e4-92d7-4401-af67-a39daeec9e0b"
NOTION_CLAUDE_REGIME_DATABASE_ID = "3efd828b-84a7-4966-8bdf-fe9c93657edd"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def inspect_db(name, db_id):
    url = f"https://api.notion.com/v1/databases/{db_id}"
    res = requests.get(url, headers=HEADERS)
    print(f"\n--- {name} ({db_id}) --- Status: {res.status_code}")
    if res.status_code == 200:
        props = res.json().get("properties", {})
        for prop_name, prop_data in props.items():
            print(f"  Property: [{prop_name}] -> Type: {prop_data.get('type')}")
    else:
        print(f"Error: {res.text}")

if __name__ == "__main__":
    inspect_db("Close Diário — Todos os Ativos — Claude", NOTION_CLAUDE_CLOSE_DATABASE_ID)
    inspect_db("Resumo Diário — Regime de Risco — Claude", NOTION_CLAUDE_REGIME_DATABASE_ID)
