#!/usr/bin/env python3
"""
Gera o PINTEREST_REFRESH_TOKEN (vale 1 ano). Rode no seu computador:

    python ferramentas/pinterest_token.py

Antes, no painel do app do Pinterest (developers.pinterest.com > My apps),
cadastre a Redirect URI:  https://localhost/
"""
import urllib.parse

import requests

REDIRECT = "https://localhost/"
ESCOPOS = "boards:read,pins:read,pins:write,user_accounts:read"

app_id = input("App ID do Pinterest: ").strip()
app_secret = input("App secret do Pinterest: ").strip()

url = "https://www.pinterest.com/oauth/?" + urllib.parse.urlencode(
    {"client_id": app_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": ESCOPOS}
)
print("\n1) Abra este link no navegador, logado no Pinterest do Radar dos Achados, e clique em Permitir:\n")
print(url)
print("\n2) O navegador vai abrir uma página de erro em 'localhost'. Isso é normal.")
print("   Copie da barra de endereço o valor depois de 'code=' (até o '&', se houver).\n")
codigo = input("Cole o code aqui: ").strip()

r = requests.post(
    "https://api.pinterest.com/v5/oauth/token",
    auth=(app_id, app_secret),
    data={"grant_type": "authorization_code", "code": codigo, "redirect_uri": REDIRECT},
    timeout=30,
)
r.raise_for_status()
dados = r.json()
token = dados["access_token"]
print("\n✅ Deu certo! Cadastre no GitHub (Settings > Secrets):")
print(f"   PINTEREST_REFRESH_TOKEN = {dados['refresh_token']}")
print(f"   PINTEREST_APP_ID        = {app_id}")
print(f"   PINTEREST_APP_SECRET    = (o secret que você digitou)")

print("\nSuas pastas (use o id no config.json, campo board_id):")
for base in ("https://api.pinterest.com/v5", "https://api-sandbox.pinterest.com/v5"):
    try:
        pastas = requests.get(f"{base}/boards", headers={"Authorization": f"Bearer {token}"}, timeout=30).json()
        for b in pastas.get("items", []):
            print(f"   {b['id']}  →  {b['name']}  ({'sandbox' if 'sandbox' in base else 'produção'})")
        if "sandbox" in base and not pastas.get("items"):
            nova = requests.post(f"{base}/boards", json={"name": "Radar dos Achados (teste)"},
                                 headers={"Authorization": f"Bearer {token}"}, timeout=30).json()
            print(f"   {nova.get('id')}  →  pasta de teste criada no sandbox")
    except Exception as e:
        print(f"   ({base}: {e})")
