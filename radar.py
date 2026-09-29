#!/usr/bin/env python3
"""
Radar dos Achados: busca ofertas na Shopee e publica no Instagram, no Facebook e no Pinterest.

Comandos:
  python radar.py preparar   -> escolhe uma oferta, gera a arte e atualiza a vitrine
  python radar.py publicar   -> publica a oferta preparada no Instagram/Pinterest
  python radar.py teste      -> busca ofertas e mostra a legenda, sem publicar nada

Variáveis de ambiente (no GitHub ficam em Settings > Secrets):
  SHOPEE_APP_ID, SHOPEE_SECRET
  IG_TOKEN, IG_USER_ID (opcional)
  FB_PAGE_TOKEN, FB_PAGE_ID (opcional)  -> Página do Facebook
  PINTEREST_ACCESS_TOKEN  ou  PINTEREST_REFRESH_TOKEN + PINTEREST_APP_ID + PINTEREST_APP_SECRET
  DRY_RUN=1  -> não publica, só simula
"""
import hashlib
import html
import io
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

RAIZ = Path(__file__).resolve().parent
CONFIG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
ARQ_ESTADO = RAIZ / "data" / "estado.json"
PASTA_SITE = RAIZ / "docs"
FUSO_BR = timezone(timedelta(hours=-3))
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
SHOPEE_URL = "https://open-api.affiliate.shopee.com.br/graphql"
UA = {"User-Agent": "Mozilla/5.0 (RadarDosAchados)"}


# ----------------------------------------------------------------- utilidades
def log(msg):
    print(f"[{datetime.now(FUSO_BR):%H:%M:%S}] {msg}", flush=True)


def agora():
    return datetime.now(FUSO_BR)


def brl(valor):
    s = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def carregar_estado():
    if ARQ_ESTADO.exists():
        return json.loads(ARQ_ESTADO.read_text(encoding="utf-8"))
    return {"contador": 0, "postados": {}, "precos": {}, "pendente": None, "ofertas": []}


def salvar_estado(estado):
    ARQ_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ARQ_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def urls_publicas():
    """Endereços públicos do repositório (imagens) e do site (vitrine)."""
    repo = os.getenv("GITHUB_REPOSITORY", "usuario/radar-dos-achados")
    dono, nome = repo.split("/")
    branch = os.getenv("GITHUB_REF_NAME", "main")
    raw = os.getenv("RAW_BASE", f"https://raw.githubusercontent.com/{repo}/{branch}")
    site = os.getenv("SITE_URL", f"https://{dono.lower()}.github.io/{nome}")
    return raw.rstrip("/"), site.rstrip("/")


# ------------------------------------------------------------------- Shopee
def shopee(query):
    app_id = os.environ["SHOPEE_APP_ID"]
    secret = os.environ["SHOPEE_SECRET"]
    payload = json.dumps({"query": query}, separators=(",", ":"), ensure_ascii=False)
    ts = str(int(time.time()))
    assinatura = hashlib.sha256((app_id + ts + payload + secret).encode("utf-8")).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={assinatura}",
    }
    r = requests.post(SHOPEE_URL, data=payload.encode("utf-8"), headers=headers, timeout=30)
    r.raise_for_status()
    dados = r.json()
    if dados.get("errors"):
        raise RuntimeError(f"Erro da API Shopee: {dados['errors']}")
    return dados["data"]


def buscar_produtos(palavra, limite):
    palavra_json = json.dumps(palavra, ensure_ascii=False)
    q = (
        "{productOfferV2(keyword:%s, sortType:2, page:1, limit:%d){nodes{"
        "itemId productName priceMin priceMax priceDiscountRate imageUrl offerLink "
        "productLink sales ratingStar shopName commissionRate}}}" % (palavra_json, limite)
    )
    return shopee(q)["productOfferV2"]["nodes"] or []


def link_curto(url_produto):
    q = 'mutation{generateShortLink(input:{originUrl:%s, subIds:["radar"]}){shortLink}}' % json.dumps(url_produto)
    return shopee(q)["generateShortLink"]["shortLink"]


def normalizar(p):
    preco = float(p.get("priceMin") or 0)
    desconto = int(p.get("priceDiscountRate") or 0)
    original = round(preco / (1 - desconto / 100), 2) if 0 < desconto < 100 else None
    return {
        "id": str(p["itemId"]),
        "nome": re.sub(r"\s+", " ", p.get("productName") or "").strip(),
        "preco": preco,
        "preco_original": original,
        "desconto": desconto,
        "imagem": p.get("imageUrl"),
        "link": p.get("offerLink"),
        "link_produto": p.get("productLink"),
        "vendas": int(p.get("sales") or 0),
        "nota": float(p.get("ratingStar") or 0),
        "loja": p.get("shopName") or "",
    }


LOGO = RAIZ / "docs" / "logo.png"


def categoria_de(p):
    """Descobre a aba da vitrine pela palavra-chave da busca ou pelo nome do produto."""
    texto = f"{p.get('palavra', '')} {p.get('nome', '')}".lower()
    for cat, termos in CONFIG.get("categorias", {}).items():
        if any(t.lower() in texto for t in termos):
            return cat
    return "Outros"


def escolher_oferta(estado):
    f = CONFIG["filtros"]
    limite_data = agora() - timedelta(days=f["dias_sem_repetir"])
    palavras = random.sample(CONFIG["palavras_chave"], k=min(CONFIG["palavras_por_execucao"], len(CONFIG["palavras_chave"])))
    candidatos = []
    for palavra in palavras:
        try:
            nos = buscar_produtos(palavra, CONFIG["produtos_por_palavra"])
            log(f"'{palavra}': {len(nos)} produtos encontrados")
        except Exception as e:  # uma palavra com erro não derruba a execução
            log(f"Erro buscando '{palavra}': {e}")
            continue
        for bruto in nos:
            p = normalizar(bruto)
            p["palavra"] = palavra
            p["categoria"] = categoria_de(p)
            candidatos.append(p)

    def aprovado(p, exigir_desconto=True):
        nome = p["nome"].lower()
        if any(x in nome for x in f["palavras_proibidas"]):
            return False
        if not (p["imagem"] and (p["link"] or p["link_produto"])):
            return False
        if not (f["preco_minimo"] <= p["preco"] <= f["preco_maximo"]):
            return False
        if p["nota"] < f["nota_minima"] or p["vendas"] < f["vendas_minimas"]:
            return False
        if exigir_desconto and p["desconto"] < f["desconto_minimo"]:
            return False
        if p["desconto"] > f.get("desconto_maximo", 100):
            return False  # desconto exagerado costuma ter "preço original" inflado
        antigo = estado["postados"].get(p["id"])
        if antigo and datetime.fromisoformat(antigo["data"]) > limite_data:
            return False
        return True

    bons = [p for p in candidatos if aprovado(p)]
    if not bons:
        log("Nenhuma oferta com o desconto mínimo; aceitando sem desconto.")
        bons = [p for p in candidatos if aprovado(p, exigir_desconto=False)]
    if not bons:
        return None

    # Pontuação: desconto + popularidade + nota, com um pouco de sorte para variar
    for p in bons:
        p["pontos"] = p["desconto"] * 1.5 + math.log10(p["vendas"] + 1) * 10 + (p["nota"] - 4) * 20 + random.uniform(0, 15)
    bons.sort(key=lambda p: p["pontos"], reverse=True)
    escolhida = bons[0]

    # Detecta queda de preço em relação à última vez que vimos o produto
    historico = estado["precos"].get(escolhida["id"], [])
    if historico:
        anterior = historico[-1]["preco"]
        if escolhida["preco"] < anterior * 0.95:
            escolhida["baixou_de"] = anterior
    if not escolhida["link"]:
        escolhida["link"] = link_curto(escolhida["link_produto"])
    return escolhida


# ------------------------------------------------------------------ legenda
ABERTURAS = [
    "🚨 Achado detectado no radar!",
    "📡 O radar apitou!",
    "✨ Achadinho do dia!",
    "🔥 Tá barato demais pra deixar passar!",
    "👀 Olha o que o radar encontrou:",
    "💥 Oferta fresquinha!",
]


def nome_curto(nome, limite=90):
    return nome if len(nome) <= limite else nome[: limite - 1].rsplit(" ", 1)[0] + "…"


def montar_legenda(p):
    linhas = [random.choice(ABERTURAS), "", f"🛍️ {nome_curto(p['nome'])}", ""]
    if p.get("baixou_de"):
        linhas.append(f"📉 BAIXOU! Estava {brl(p['baixou_de'])}")
    if p["preco_original"]:
        linhas.append(f"💸 De {brl(p['preco_original'])} por {brl(p['preco'])} ({p['desconto']}% OFF)")
    else:
        linhas.append(f"💸 Por apenas {brl(p['preco'])}")
    linhas.append(f"⭐ {p['nota']:.1f} • +{p['vendas']} vendidos")
    linhas += [
        "",
        f"🔗 Link na bio → procure o achado nº {p['numero']}",
        "",
        "⚠️ Preço e estoque podem mudar a qualquer momento.",
        "#publi • link de afiliado",
        "",
        CONFIG["instagram"]["hashtags"],
    ]
    return "\n".join(linhas)


# -------------------------------------------------------------------- arte
COR_FUNDO = (255, 247, 240)
COR_MARCA = (238, 77, 45)  # laranja Shopee-like
COR_TEXTO = (33, 33, 33)
COR_SUAVE = (120, 120, 120)
COR_AZUL = (1, 33, 67)  # azul-marinho do logo


def fonte(tamanho, peso="Bold"):
    candidatas = [
        RAIZ / "fontes" / f"Poppins-{peso}.ttf",
        Path("/usr/share/fonts/truetype/dejavu/" + ("DejaVuSans.ttf" if peso == "Regular" else "DejaVuSans-Bold.ttf")),
    ]
    for c in candidatas:
        if c.exists():
            return ImageFont.truetype(str(c), tamanho)
    return ImageFont.load_default(size=tamanho)


def quebrar(draw, texto, fnt, largura, max_linhas):
    palavras, linhas, atual = texto.split(), [], ""
    for w in palavras:
        teste = f"{atual} {w}".strip()
        if draw.textlength(teste, font=fnt) <= largura:
            atual = teste
        else:
            linhas.append(atual)
            atual = w
            if len(linhas) == max_linhas:
                break
    if len(linhas) < max_linhas and atual:
        linhas.append(atual)
    if len(linhas) == max_linhas and " ".join(linhas) != texto:
        while draw.textlength(linhas[-1] + "…", font=fnt) > largura:
            linhas[-1] = linhas[-1][:-1]
        linhas[-1] += "…"
    return linhas


CHAMADAS = ["QUERO O MEU", "CORRE, QUE ACABA", "PEGA O SEU", "APROVEITA"]


def gerar_arte(p, destino):
    """Arte 1080x1350: produto em destaque, marca discreta e chamada para ação."""
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), COR_FUNDO)
    d = ImageDraw.Draw(img)

    # Faixa fina da marca
    d.rectangle([0, 0, W, 84], fill=COR_MARCA)
    x_nome = 40
    if LOGO.exists():
        logo = ImageOps.contain(Image.open(LOGO).convert("RGBA"), (68, 68), Image.LANCZOS)
        img.paste(logo, (40, 42 - logo.height // 2), logo)
        x_nome = 124
    d.text((x_nome, 42), CONFIG["nome_perfil"], font=fonte(30, "Bold"), fill="white", anchor="lm")

    # Foto do produto, grande e inteira (sem cortar)
    resp = requests.get(p["imagem"], headers=UA, timeout=30)
    resp.raise_for_status()
    foto = Image.open(io.BytesIO(resp.content)).convert("RGB")
    CX, CY, CW, CH = 40, 108, 1000, 870
    foto = ImageOps.contain(foto, (CW - 40, CH - 40), Image.LANCZOS)
    caixa = Image.new("RGB", (CW, CH), "white")
    caixa.paste(foto, ((CW - foto.width) // 2, (CH - foto.height) // 2))
    mascara = Image.new("L", caixa.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle([0, 0, CW, CH], radius=36, fill=255)
    img.paste(caixa, (CX, CY), mascara)

    # Selo de desconto
    if p["desconto"]:
        sx, sy, sr = W - 130, CY + 110, 88
        d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=COR_MARCA)
        d.text((sx, sy - 16), f"-{p['desconto']}%", font=fonte(50, "ExtraBold"), fill="white", anchor="mm")
        d.text((sx, sy + 32), "OFF", font=fonte(30, "Bold"), fill="white", anchor="mm")

    # Nome (1 linha)
    f_nome = fonte(34, "Regular")
    d.text((50, 1000), quebrar(d, p["nome"], f_nome, W - 100, 1)[0], font=f_nome, fill=COR_TEXTO)

    # Preços
    y = 1058
    if p["preco_original"]:
        txt = f"de {brl(p['preco_original'])}"
        f_de = fonte(34, "Regular")
        d.text((50, y), txt, font=f_de, fill=COR_SUAVE)
        d.line([50 + d.textlength("de ", font=f_de), y + 22, 50 + d.textlength(txt, font=f_de), y + 22], fill=COR_SUAVE, width=3)
        y += 46
    d.text((50, y), f"por {brl(p['preco'])}", font=fonte(76, "ExtraBold"), fill=COR_MARCA)

    # Chamada para ação + número discreto
    chamada = random.choice(CHAMADAS)
    f_cta = fonte(32, "ExtraBold")
    larg = int(d.textlength(chamada + "  →", font=f_cta)) + 80
    bx1, by0 = W - 50, 1226
    d.rounded_rectangle([bx1 - larg, by0, bx1, by0 + 84], radius=42, fill=COR_AZUL)
    d.text((bx1 - larg / 2, by0 + 42), chamada + "  →", font=f_cta, fill="white", anchor="mm")
    d.text((50, by0 + 42), f"achado nº {p['numero']} • link na bio", font=fonte(28, "Regular"), fill=COR_SUAVE, anchor="lm")

    destino.parent.mkdir(parents=True, exist_ok=True)
    img.save(destino, "JPEG", quality=90)


# ------------------------------------------------------------------ vitrine
ESTILO = """
:root{--marca:#ee4d2d;--fundo:#fff7f0;--texto:#212121;--suave:#777;--card:#fff}
@media (prefers-color-scheme:dark){:root{--fundo:#161312;--texto:#f2f2f2;--suave:#aaa;--card:#221e1c}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--fundo);color:var(--texto)}
header{background:var(--marca);color:#fff;padding:22px 16px;text-align:center}
header h1{margin:0;font-size:1.6rem;letter-spacing:.04em}
.logo{width:132px;height:132px;display:block;margin:0 auto 6px;filter:drop-shadow(0 2px 6px rgba(0,0,0,.25))}
.abas{display:flex;gap:8px;overflow-x:auto;padding:12px 16px;position:sticky;top:0;z-index:5;background:var(--fundo);box-shadow:0 2px 6px rgba(0,0,0,.06);max-width:980px;margin:0 auto}
.aba{flex:0 0 auto;border:2px solid var(--marca);background:var(--card);color:var(--texto);border-radius:999px;padding:8px 16px;font-weight:700;font-size:.9rem;cursor:pointer}
.aba.ativa{background:#012143;border-color:#012143;color:#fff}header p{margin:6px 0 0;opacity:.9}
main{max-width:980px;margin:0 auto;padding:16px}
input{width:100%;padding:14px;border-radius:12px;border:2px solid var(--marca);font-size:1rem;margin-bottom:16px;background:var(--card);color:var(--texto)}
.grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.card{background:var(--card);border-radius:14px;overflow:hidden;display:flex;flex-direction:column;text-decoration:none;color:inherit;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.card img{width:100%;aspect-ratio:1;object-fit:cover;background:#fff}
.info{padding:10px;display:flex;flex-direction:column;gap:4px;flex:1}
.num{font-size:.8rem;font-weight:700;color:var(--marca)}.nome{font-size:.85rem;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.de{font-size:.75rem;color:var(--suave);text-decoration:line-through}.por{font-weight:800;font-size:1.05rem}
.btn{margin-top:auto;background:var(--marca);color:#fff;text-align:center;padding:8px;border-radius:10px;font-weight:700;font-size:.85rem}
footer{text-align:center;color:var(--suave);font-size:.8rem;padding:24px 16px}
.unico{max-width:480px;margin:0 auto;padding:16px;text-align:center}.unico img{width:100%;border-radius:16px;background:#fff}
.unico .btn{display:block;padding:16px;font-size:1.1rem;text-decoration:none;margin-top:16px}
"""

AVISO = "Links de afiliado: posso receber comissão pelas compras, sem custo extra pra você. Preços podem mudar."


def cartao_html(o):
    de = f'<span class="de">{brl(o["preco_original"])}</span>' if o.get("preco_original") else ""
    return (
        f'<a class="card" href="{html.escape(o["link"])}" target="_blank" rel="nofollow sponsored noopener" data-n="{o["numero"]}" data-cat="{html.escape(o.get("categoria") or "Outros")}">'
        f'<img loading="lazy" src="{html.escape(o["imagem"])}" alt="">'
        f'<div class="info"><span class="num">nº {o["numero"]}</span><span class="nome">{html.escape(o["nome"])}</span>'
        f'{de}<span class="por">{brl(o["preco"])}</span><span class="btn">Ver na Shopee</span></div></a>'
    )


def gerar_vitrine(estado):
    nome = html.escape(CONFIG["nome_perfil"])
    ofertas = estado["ofertas"][: CONFIG["vitrine"]["quantidade_na_pagina"]]
    for o in ofertas:  # ofertas antigas (ou categoria nova no config) são reclassificadas
        o["categoria"] = categoria_de(o)
    cards = "\n".join(cartao_html(o) for o in ofertas)
    marca = f'<h1><img class="logo" src="logo.png" alt="{nome}"></h1>' if LOGO.exists() else f"<h1>📡 {nome}</h1>"
    nomes_abas = ["Todos"] + list(CONFIG.get("categorias", {}))
    if any(o["categoria"] == "Outros" for o in ofertas):
        nomes_abas.append("Outros")
    abas = "".join(
        f'<button class="aba{" ativa" if i == 0 else ""}" data-aba="{html.escape(a)}">{html.escape(a)}</button>'
        for i, a in enumerate(nomes_abas)
    )
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{nome}</title>
<meta name="description" content="Os melhores achadinhos da Shopee, atualizados todo dia.">
<style>{ESTILO}</style></head><body>
<header>{marca}<p>Digite o número do achado que você viu no post</p></header>
<nav class="abas" id="abas">{abas}</nav>
<main><input id="busca" type="search" inputmode="numeric" placeholder="Ex.: 123 ou fone">
<div class="grade" id="grade">{cards}</div></main>
<footer>{AVISO}<br>Atualizado em {agora():%d/%m/%Y %H:%M} • <a href="privacidade.html">Privacidade</a></footer>
<script>
const b=document.getElementById('busca');let aba='Todos';
function filtrar(){{const t=b.value.trim().toLowerCase();
document.querySelectorAll('.card').forEach(c=>{{const okBusca=!t||c.dataset.n===t.replace('#','')||c.textContent.toLowerCase().includes(t);
const okAba=aba==='Todos'||c.dataset.cat===aba;c.style.display=(okBusca&&(t||okAba))?'':'none';}});}}
b.addEventListener('input',filtrar);
document.querySelectorAll('.aba').forEach(x=>x.addEventListener('click',()=>{{aba=x.dataset.aba;
document.querySelectorAll('.aba').forEach(y=>y.classList.toggle('ativa',y===x));filtrar();}}));
</script></body></html>"""
    PASTA_SITE.mkdir(parents=True, exist_ok=True)
    (PASTA_SITE / "index.html").write_text(pagina, encoding="utf-8")
    (PASTA_SITE / ".nojekyll").write_text("", encoding="utf-8")

    priv = PASTA_SITE / "privacidade.html"
    if not priv.exists():
        priv.write_text(f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Privacidade • {nome}</title><style>{ESTILO}</style></head>
<body><main class="unico" style="text-align:left"><h1>Política de privacidade</h1>
<p>O {nome} é um projeto pessoal de divulgação de ofertas. Este site não coleta, armazena nem compartilha dados pessoais dos visitantes.</p>
<p>A automação usa as APIs do Instagram e do Pinterest apenas para publicar conteúdo na conta do próprio {nome}. Nenhum dado de outros usuários é acessado.</p>
<p>Os links levam à Shopee e são links de afiliado. Ao clicar, valem as políticas de privacidade da Shopee.</p>
<p>Contato: pelo direct do Instagram {html.escape(CONFIG['arroba_instagram'])}.</p></main></body></html>""", encoding="utf-8")


def gerar_pagina_oferta(o, url_arte):
    """Página individual usada como link dos pins do Pinterest."""
    nome = html.escape(o["nome"])
    de = f'<p class="de">{brl(o["preco_original"])}</p>' if o.get("preco_original") else ""
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{nome}</title>
<meta property="og:title" content="{nome}"><meta property="og:image" content="{html.escape(url_arte)}">
<meta property="og:description" content="Por {brl(o['preco'])} na Shopee">
<style>{ESTILO}</style></head><body><header>{'<img class="logo" src="../logo.png" alt="">' if LOGO.exists() else '📡 '}<h1>{html.escape(CONFIG['nome_perfil'])}</h1></header>
<main class="unico"><img src="{html.escape(o['imagem'])}" alt="{nome}"><h2>{nome}</h2>{de}
<p class="por" style="font-size:1.6rem;color:var(--marca)">{brl(o['preco'])}</p>
<a class="btn" href="{html.escape(o['link'])}" rel="nofollow sponsored noopener">Ver oferta na Shopee</a>
<p style="margin-top:24px"><a href="../index.html">Ver todos os achados</a></p></main>
<footer>{AVISO}</footer></body></html>"""
    destino = PASTA_SITE / "o" / f"{o['numero']}.html"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(pagina, encoding="utf-8")


# ---------------------------------------------------------------- Instagram
def publicar_instagram(p, url_arte):
    # Caminho 1 (preferido): token da Página do Facebook + IG_USER_ID (não vence).
    # Caminho 2: token do login do Instagram (IG_TOKEN, vence em 60 dias).
    ig_id = os.getenv("IG_USER_ID")
    if os.getenv("FB_PAGE_TOKEN") and ig_id:
        token = os.environ["FB_PAGE_TOKEN"]
        base = "https://graph.facebook.com/v23.0"
    else:
        token = os.environ["IG_TOKEN"]
        base = "https://graph.instagram.com/v23.0"
    if not ig_id:
        r = requests.get(f"{base}/me", params={"fields": "user_id,username", "access_token": token}, timeout=30)
        r.raise_for_status()
        ig_id = r.json()["user_id"]
    r = requests.post(f"{base}/{ig_id}/media", data={"image_url": url_arte, "caption": p["legenda"], "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Instagram (container): {r.text}")
    container = r.json()["id"]
    for _ in range(12):
        time.sleep(5)
        st = requests.get(f"{base}/{container}", params={"fields": "status_code", "access_token": token}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram recusou a imagem: {st}")
    r = requests.post(f"{base}/{ig_id}/media_publish", data={"creation_id": container, "access_token": token}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Instagram (publicar): {r.text}")
    return r.json()["id"]


# ----------------------------------------------------------------- Facebook
def legenda_facebook(p):
    """No Facebook o link é clicável, então vai o link direto da oferta."""
    cfg = CONFIG.get("facebook", {})
    linhas = [random.choice(ABERTURAS), "", f"🛍️ {nome_curto(p['nome'])}", ""]
    if p.get("baixou_de"):
        linhas.append(f"📉 BAIXOU! Estava {brl(p['baixou_de'])}")
    if p["preco_original"]:
        linhas.append(f"💸 De {brl(p['preco_original'])} por {brl(p['preco'])} ({p['desconto']}% OFF)")
    else:
        linhas.append(f"💸 Por apenas {brl(p['preco'])}")
    linhas.append(f"⭐ {p['nota']:.1f} • +{p['vendas']} vendidos")
    linhas += ["", f"👉 Comprar: {p['link']}"]
    if cfg.get("link_grupo"):
        linhas.append(f"📲 Mais achados todo dia no grupo: {cfg['link_grupo']}")
    linhas += [
        "",
        "⚠️ Preço e estoque podem mudar a qualquer momento.",
        "#publi • link de afiliado",
        "",
        cfg.get("hashtags", ""),
    ]
    return "\n".join(linhas).strip()


def publicar_facebook(p, url_arte):
    token = os.environ["FB_PAGE_TOKEN"]
    base = "https://graph.facebook.com/v23.0"
    page_id = os.getenv("FB_PAGE_ID")
    if not page_id:
        r = requests.get(f"{base}/me", params={"fields": "id,name", "access_token": token}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Facebook (token): {r.text}")
        page_id = r.json()["id"]
    r = requests.post(
        f"{base}/{page_id}/photos",
        data={"url": url_arte, "message": legenda_facebook(p), "published": "true", "access_token": token},
        timeout=90,
    )
    if not r.ok:
        raise RuntimeError(f"Facebook (post): {r.text}")
    resp = r.json()
    post_id = resp.get("post_id") or resp["id"]
    cfg = CONFIG.get("facebook", {})
    if cfg.get("comentario_com_link", True):
        texto = f"🛒 Link da oferta: {p['link']}"
        if cfg.get("link_grupo"):
            texto += f"\n📲 Grupo de achados: {cfg['link_grupo']}"
        c = requests.post(f"{base}/{post_id}/comments", data={"message": texto, "access_token": token}, timeout=60)
        if not c.ok:
            log(f"Aviso: post saiu, mas o comentário com o link falhou: {c.text}")
    return post_id


# ---------------------------------------------------------------- Pinterest
def token_pinterest():
    if os.getenv("PINTEREST_REFRESH_TOKEN"):
        r = requests.post(
            "https://api.pinterest.com/v5/oauth/token",
            auth=(os.environ["PINTEREST_APP_ID"], os.environ["PINTEREST_APP_SECRET"]),
            data={"grant_type": "refresh_token", "refresh_token": os.environ["PINTEREST_REFRESH_TOKEN"]},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["access_token"]
    return os.environ["PINTEREST_ACCESS_TOKEN"]


def publicar_pinterest(p, url_arte, url_pagina):
    cfg = CONFIG["pinterest"]
    base = "https://api-sandbox.pinterest.com/v5" if cfg.get("sandbox") else "https://api.pinterest.com/v5"
    titulo = nome_curto(p["nome"], 100)
    preco = f"De {brl(p['preco_original'])} por {brl(p['preco'])} ({p['desconto']}% OFF)" if p["preco_original"] else f"Por {brl(p['preco'])}"
    descricao = f"{preco}. ⭐ {p['nota']:.1f} e +{p['vendas']} vendidos na Shopee. Achado nº {p['numero']} do {CONFIG['nome_perfil']}. #publi link de afiliado"
    corpo = {
        "board_id": cfg["board_id"],
        "title": titulo,
        "description": descricao[:500],
        "link": url_pagina,
        "alt_text": titulo[:500],
        "media_source": {"source_type": "image_url", "url": url_arte},
    }
    r = requests.post(f"{base}/pins", json=corpo, headers={"Authorization": f"Bearer {token_pinterest()}"}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Pinterest: {r.text}")
    return r.json()["id"]


# ----------------------------------------------------------------- comandos
def preparar():
    estado = carregar_estado()
    if estado.get("pendente"):
        log(f"Já existe um achado pendente (nº {estado['pendente']['numero']}); nada a preparar.")
        return
    p = escolher_oferta(estado)
    if not p:
        log("Nenhuma oferta passou nos filtros desta vez.")
        return
    estado["contador"] += 1
    p["numero"] = estado["contador"]
    p["legenda"] = montar_legenda(p)
    arquivo_arte = PASTA_SITE / "img" / f"{p['numero']}.jpg"
    gerar_arte(p, arquivo_arte)

    oferta_vitrine = {k: p[k] for k in ("numero", "id", "nome", "preco", "preco_original", "desconto", "imagem", "link")}
    oferta_vitrine["categoria"] = p.get("categoria", "Outros")
    raw, site = urls_publicas()
    estado["ofertas"].insert(0, oferta_vitrine)
    estado["ofertas"] = estado["ofertas"][:200]
    gerar_pagina_oferta(oferta_vitrine, f"{raw}/docs/img/{p['numero']}.jpg")
    gerar_vitrine(estado)

    estado["precos"].setdefault(p["id"], []).append({"data": agora().isoformat(), "preco": p["preco"]})
    estado["precos"][p["id"]] = estado["precos"][p["id"]][-10:]
    estado["postados"][p["id"]] = {"data": agora().isoformat(), "numero": p["numero"]}
    p["tentativas"] = 0
    estado["pendente"] = p
    salvar_estado(estado)
    log(f"Preparado achado nº {p['numero']}: {p['nome'][:60]} — {brl(p['preco'])}")
    print("\n" + p["legenda"] + "\n")


def publicar():
    estado = carregar_estado()
    p = estado.get("pendente")
    if not p:
        log("Nada pendente para publicar.")
        return
    raw, site = urls_publicas()
    url_arte = f"{raw}/docs/img/{p['numero']}.jpg"
    url_pagina = f"{site}/o/{p['numero']}.html"
    p.setdefault("publicado", {})
    erros = []

    if DRY_RUN:
        log(f"[SIMULAÇÃO] Publicaria {url_arte}")
        if CONFIG.get("facebook", {}).get("ativo"):
            log("[SIMULAÇÃO] Legenda do Facebook:\n" + legenda_facebook(p))
        estado["pendente"] = None
        salvar_estado(estado)
        return

    if CONFIG["instagram"]["ativo"] and "instagram" not in p["publicado"]:
        try:
            p["publicado"]["instagram"] = publicar_instagram(p, url_arte)
            log(f"Instagram OK: {p['publicado']['instagram']}")
        except Exception as e:
            erros.append(str(e))
            log(f"ERRO Instagram: {e}")

    fb_cfg = CONFIG.get("facebook", {})
    if fb_cfg.get("ativo") and "facebook" not in p["publicado"]:
        if not os.getenv("FB_PAGE_TOKEN"):
            log("Facebook: secret FB_PAGE_TOKEN ainda não cadastrado, pulando.")
        else:
            try:
                p["publicado"]["facebook"] = publicar_facebook(p, url_arte)
                log(f"Facebook OK: {p['publicado']['facebook']}")
            except Exception as e:
                erros.append(str(e))
                log(f"ERRO Facebook: {e}")

    if CONFIG["pinterest"]["ativo"] and "pinterest" not in p["publicado"]:
        try:
            p["publicado"]["pinterest"] = publicar_pinterest(p, url_arte, url_pagina)
            log(f"Pinterest OK: {p['publicado']['pinterest']}")
        except Exception as e:
            erros.append(str(e))
            log(f"ERRO Pinterest: {e}")

    p["tentativas"] = p.get("tentativas", 0) + 1
    if not erros or p["tentativas"] >= 3:
        if erros:
            log("Desistindo deste achado após 3 tentativas.")
        estado["postados"][p["id"]]["redes"] = p["publicado"]
        estado["pendente"] = None
    salvar_estado(estado)
    if erros and p["tentativas"] < 3:
        log("Vou tentar de novo na próxima execução.")


def teste():
    estado = carregar_estado()
    p = escolher_oferta(estado)
    if not p:
        log("Nenhuma oferta passou nos filtros.")
        return
    p["numero"] = estado["contador"] + 1
    print("\n" + montar_legenda(p) + "\n")
    if CONFIG.get("facebook", {}).get("ativo"):
        p.setdefault("link", "https://s.shopee.com.br/EXEMPLO")
        print("--- Facebook ---\n" + legenda_facebook(p) + "\n")
    gerar_arte(p, RAIZ / "teste_arte.jpg")
    log("Arte de teste salva em teste_arte.jpg")


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else "teste"
    {"preparar": preparar, "publicar": publicar, "teste": teste}[comando]()
