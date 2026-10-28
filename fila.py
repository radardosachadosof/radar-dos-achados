#!/usr/bin/env python3
"""
Fila curada do Radar dos Achados (planilha no Google Drive).

Comandos:
  python fila.py verificar   -> lê a planilha e mostra o que encontrou (não muda nada)
  python fila.py preparar    -> pega o 1º item "aprovado", baixa a peça do Drive e deixa pronto
  python fila.py publicar    -> publica o item preparado no Instagram e no Facebook
  python fila.py entregar    -> sobe para o Drive as peças da pasta entregas/<ID>/ e marca
                                "aguardando aprovação"

Fluxo de status na coluna "status":
  aguardando textos -> aguardando flow -> pronto para editar -> aguardando aprovação
  -> aprovado -> postado      (ou "ajustes solicitados" / "erro")
Só "aprovado" libera a postagem. Célula vazia nunca conta como aprovação.

Secrets usados: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN,
FB_PAGE_TOKEN, FB_PAGE_ID, IG_USER_ID. DRY_RUN=1 simula sem publicar.
"""
import io
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

import requests
from PIL import Image

from radar import CONFIG, DRY_RUN, RAIZ, agora, log, urls_publicas

CFG = CONFIG.get("fila", {})
ARQ_ESTADO = RAIZ / "data" / "fila_estado.json"
PASTA_TMP = RAIZ / "tmp_fila"
PASTA_PUBLICA = RAIZ / "docs" / "fila"
PASTA_ENTREGAS = RAIZ / "entregas"
GRAPH = "https://graph.facebook.com/v23.0"
RUPLOAD = "https://rupload.facebook.com"
MAX_TENTATIVAS = 3


# ----------------------------------------------------------------- utilidades
def simples(txt):
    """Minúsculas e sem acento, para comparar status e cabeçalhos."""
    txt = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", txt).strip().lower()


def id_do_link(link):
    """Extrai o ID de um link do Drive (arquivo ou pasta) ou aceita o ID puro."""
    link = (link or "").strip()
    for padrao in (r"/d/([\w-]{20,})", r"/folders/([\w-]{20,})", r"[?&]id=([\w-]{20,})"):
        m = re.search(padrao, link)
        if m:
            return m.group(1)
    return link if re.fullmatch(r"[\w-]{20,}", link) else None


def carregar_estado():
    if ARQ_ESTADO.exists():
        return json.loads(ARQ_ESTADO.read_text(encoding="utf-8"))
    return {"pendente": None, "itens": {}}


def salvar_estado(estado):
    ARQ_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ARQ_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def letra_coluna(n):
    """0 -> A, 25 -> Z, 26 -> AA"""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# --------------------------------------------------------------------- Google
_token = {"valor": None, "expira": 0}


def google_token():
    if _token["valor"] and time.time() < _token["expira"] - 60:
        return _token["valor"]
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
            "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"].strip(),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Google recusou a autorização: {r.text}")
    d = r.json()
    _token.update(valor=d["access_token"], expira=time.time() + int(d.get("expires_in", 3600)))
    return _token["valor"]


def google(metodo, url, **kw):
    kw.setdefault("timeout", 60)
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {google_token()}"
    r = requests.request(metodo, url, headers=headers, **kw)
    if not r.ok:
        raise RuntimeError(f"Google {metodo} {url.split('?')[0]}: {r.status_code} {r.text[:400]}")
    return r


# ------------------------------------------------------------------- planilha
class Planilha:
    def __init__(self):
        self.id = CFG["planilha_id"]
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{self.id}"
        self.base = base
        info = google("GET", base, params={"fields": "sheets.properties.title"}).json()
        self.aba = CFG.get("aba") or info["sheets"][0]["properties"]["title"]
        intervalo = f"'{self.aba}'!A1:AZ1000"
        valores = google("GET", f"{base}/values/{requests.utils.quote(intervalo)}").json().get("values", [])
        if not valores:
            raise RuntimeError("A planilha está vazia.")
        self.cabecalho = [simples(c) for c in valores[0]]
        self.linhas = []
        for i, bruta in enumerate(valores[1:], start=2):
            bruta = bruta + [""] * (len(self.cabecalho) - len(bruta))
            item = {self.cabecalho[j]: (bruta[j] or "").strip() for j in range(len(self.cabecalho)) if self.cabecalho[j]}
            if item.get("id"):
                item["_linha"] = i
                self.linhas.append(item)

    def coluna(self, nome):
        nome = simples(nome)
        if nome not in self.cabecalho:  # cria a coluna no fim, se não existir
            self.cabecalho.append(nome)
            self._escrever(f"{letra_coluna(len(self.cabecalho) - 1)}1", nome)
        return letra_coluna(self.cabecalho.index(nome))

    def _escrever(self, celula, valor):
        intervalo = f"'{self.aba}'!{celula}"
        google(
            "PUT",
            f"{self.base}/values/{requests.utils.quote(intervalo)}",
            params={"valueInputOption": "USER_ENTERED"},
            json={"values": [[valor]]},
        )

    def atualizar(self, item, **campos):
        for nome, valor in campos.items():
            if DRY_RUN:
                log(f"[SIMULAÇÃO] {item['id']}: {nome} = {valor}")
                continue
            self._escrever(f"{self.coluna(nome)}{item['_linha']}", valor)
            item[simples(nome)] = valor

    def por_id(self, ident):
        return next((l for l in self.linhas if simples(l["id"]) == simples(ident)), None)


# ---------------------------------------------------------------------- Drive
DRIVE = "https://www.googleapis.com/drive/v3/files"


def drive_info(file_id):
    return google("GET", f"{DRIVE}/{file_id}", params={"fields": "id,name,mimeType,webViewLink,parents"}).json()


def drive_listar(pasta_id):
    q = f"'{pasta_id}' in parents and trashed=false"
    return google("GET", DRIVE, params={"q": q, "fields": "files(id,name,mimeType,webViewLink)", "pageSize": 200}).json()["files"]


def drive_baixar(file_id, destino):
    destino.parent.mkdir(parents=True, exist_ok=True)
    r = google("GET", f"{DRIVE}/{file_id}", params={"alt": "media"}, stream=True, timeout=300)
    with open(destino, "wb") as f:
        for bloco in r.iter_content(1 << 20):
            f.write(bloco)
    return destino


def drive_subir(arquivo, pasta_id, nome=None):
    """Sobe um arquivo para a pasta do Drive (substitui se já existir com o mesmo nome)."""
    nome = nome or arquivo.name
    tipo = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(
        arquivo.suffix.lower(), "application/octet-stream")
    existente = next((f for f in drive_listar(pasta_id) if f["name"] == nome), None)
    if existente:
        url = f"https://www.googleapis.com/upload/drive/v3/files/{existente['id']}?uploadType=resumable&fields=id,webViewLink"
        r = google("PATCH", url, json={}, headers={"X-Upload-Content-Type": tipo})
    else:
        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,webViewLink"
        r = google("POST", url, json={"name": nome, "parents": [pasta_id]}, headers={"X-Upload-Content-Type": tipo})
    destino = r.headers["Location"]
    dados = arquivo.read_bytes()
    r2 = requests.put(destino, data=dados, headers={"Content-Type": tipo}, timeout=600)
    if not r2.ok:
        raise RuntimeError(f"Drive (upload {nome}): {r2.status_code} {r2.text[:300]}")
    return r2.json()


def drive_pasta(nome, pai_id):
    existente = next((f for f in drive_listar(pai_id) if f["name"] == nome and f["mimeType"].endswith("folder")), None)
    if existente:
        return existente
    return google("POST", DRIVE, params={"fields": "id,webViewLink"},
                  json={"name": nome, "parents": [pai_id], "mimeType": "application/vnd.google-apps.folder"}).json()


# ------------------------------------------------------------------- Facebook
def fb_ids():
    token = os.environ["FB_PAGE_TOKEN"]
    page_id = os.getenv("FB_PAGE_ID")
    ig_id = os.getenv("IG_USER_ID")
    if not page_id:
        page_id = requests.get(f"{GRAPH}/me", params={"access_token": token}, timeout=30).json()["id"]
    if not ig_id:
        r = requests.get(f"{GRAPH}/{page_id}", params={"fields": "instagram_business_account", "access_token": token}, timeout=30)
        ig_id = r.json()["instagram_business_account"]["id"]
    return token, page_id, ig_id


def checar(r, onde):
    if not r.ok:
        raise RuntimeError(f"{onde}: {r.text[:500]}")
    return r.json()


def ig_esperar(container, token, minutos=6):
    fim = time.time() + minutos * 60
    while time.time() < fim:
        time.sleep(8)
        st = requests.get(f"{GRAPH}/{container}", params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram recusou a mídia: {st}")
    raise RuntimeError("Instagram demorou demais para processar a mídia.")


def ig_reel(video, legenda, url_capa=None):
    token, _, ig_id = fb_ids()
    dados = {"media_type": "REELS", "upload_type": "resumable", "caption": legenda,
             "share_to_feed": "true", "access_token": token}
    if url_capa:
        dados["cover_url"] = url_capa
    container = checar(requests.post(f"{GRAPH}/{ig_id}/media", data=dados, timeout=60), "Instagram (criar Reel)")["id"]
    tamanho = video.stat().st_size
    with open(video, "rb") as f:
        r = requests.post(f"{RUPLOAD}/ig-api-upload/v23.0/{container}", data=f, timeout=600,
                          headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(tamanho)})
    checar(r, "Instagram (enviar vídeo)")
    ig_esperar(container, token)
    return checar(requests.post(f"{GRAPH}/{ig_id}/media_publish",
                                data={"creation_id": container, "access_token": token}, timeout=60),
                  "Instagram (publicar Reel)")["id"]


def ig_carrossel(urls, legenda):
    token, _, ig_id = fb_ids()
    filhos = []
    for u in urls:
        r = requests.post(f"{GRAPH}/{ig_id}/media", data={"image_url": u, "is_carousel_item": "true", "access_token": token}, timeout=60)
        filhos.append(checar(r, "Instagram (slide)")["id"])
    for c in filhos:
        ig_esperar(c, token, 3)
    r = requests.post(f"{GRAPH}/{ig_id}/media", data={"media_type": "CAROUSEL", "children": ",".join(filhos),
                                                       "caption": legenda, "access_token": token}, timeout=60)
    container = checar(r, "Instagram (carrossel)")["id"]
    ig_esperar(container, token, 3)
    return checar(requests.post(f"{GRAPH}/{ig_id}/media_publish",
                                data={"creation_id": container, "access_token": token}, timeout=60),
                  "Instagram (publicar carrossel)")["id"]


def fb_reel(video, legenda):
    token, page_id, _ = fb_ids()
    ini = checar(requests.post(f"{GRAPH}/{page_id}/video_reels",
                               data={"upload_phase": "start", "access_token": token}, timeout=60), "Facebook (iniciar Reel)")
    video_id = ini["video_id"]
    tamanho = video.stat().st_size
    with open(video, "rb") as f:
        r = requests.post(f"{RUPLOAD}/video-upload/v23.0/{video_id}", data=f, timeout=600,
                          headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(tamanho)})
    checar(r, "Facebook (enviar vídeo)")
    checar(requests.post(f"{GRAPH}/{page_id}/video_reels",
                         data={"upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
                               "description": legenda, "access_token": token}, timeout=60),
           "Facebook (publicar Reel)")
    return video_id


def fb_carrossel(urls, legenda):
    token, page_id, _ = fb_ids()
    fotos = []
    for u in urls:
        r = requests.post(f"{GRAPH}/{page_id}/photos", data={"url": u, "published": "false", "access_token": token}, timeout=90)
        fotos.append(checar(r, "Facebook (foto)")["id"])
    dados = {"message": legenda, "access_token": token}
    for i, fid in enumerate(fotos):
        dados[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
    return checar(requests.post(f"{GRAPH}/{page_id}/feed", data=dados, timeout=90), "Facebook (post)")["id"]


def legenda_fb(item):
    """No Facebook o link é clicável: acrescenta o link da oferta."""
    leg = item.get("legenda_instagram", "")
    link = item.get("link_shopee", "")
    if link and link not in leg:
        leg = leg.replace("pelo link da bio", "pelo link abaixo").replace("link na bio", "link abaixo")
        leg += f"\n\n👉 Ver o produto: {link}"
    return leg


# -------------------------------------------------------------------- comandos
def primeiro_aprovado(planilha, estado):
    for item in planilha.linhas:
        if simples(item.get("status")) == "aprovado":
            return item
    return None


def pecas_do_item(item):
    """Descobre no Drive o vídeo (reel) ou os slides (carrossel) e a capa."""
    formato = simples(item.get("formato"))
    pasta_id = id_do_link(item.get("pasta_midia"))
    peca_id = id_do_link(item.get("peca_final"))
    arquivos_pasta = drive_listar(pasta_id) if pasta_id else []
    capa = next((f for f in arquivos_pasta if "capa" in simples(f["name"]) and f["mimeType"].startswith("image/")), None)

    if formato.startswith("carross"):
        if peca_id and drive_info(peca_id)["mimeType"].endswith("folder"):
            slides = [f for f in drive_listar(peca_id) if f["mimeType"].startswith("image/")]
        else:
            slides = [f for f in arquivos_pasta if f["mimeType"].startswith("image/") and re.match(r"\d", f["name"])]
        slides.sort(key=lambda f: int(re.match(r"\d+", f["name"]).group()) if re.match(r"\d+", f["name"]) else 999)
        if not 2 <= len(slides) <= 10:
            raise RuntimeError(f"O carrossel precisa de 2 a 10 imagens numeradas (achei {len(slides)}).")
        return {"formato": "carrossel", "slides": slides}

    if peca_id:
        video = drive_info(peca_id)
    else:  # sem link: procura um vídeo com "reel" no nome (que não seja a versão da Shopee)
        video = next((f for f in arquivos_pasta if f["mimeType"].startswith("video/")
                      and "reel" in simples(f["name"]) and "shopee" not in simples(f["name"])), None)
    if not video or not video["mimeType"].startswith("video/"):
        raise RuntimeError("Não achei o vídeo do Reel: cole o link do vídeo na coluna peca_final.")
    return {"formato": "reel", "video": video, "capa": capa}


def preparar():
    if not CFG.get("ativo"):
        log("Fila curada desativada no config.json.")
        return
    estado = carregar_estado()
    if estado.get("pendente"):
        log(f"Já existe um item da fila preparado ({estado['pendente']['id']}).")
        return
    planilha = Planilha()
    item = primeiro_aprovado(planilha, estado)
    if not item:
        log("Nenhum item 'aprovado' na fila. Nada a fazer.")
        return
    ident = item["id"]
    log(f"Item da fila: {ident} ({item.get('formato')}) - {item.get('nome_curto')}")
    try:
        pecas = pecas_do_item(item)
        pasta_pub = PASTA_PUBLICA / ident
        if pasta_pub.exists():
            shutil.rmtree(pasta_pub)
        pasta_pub.mkdir(parents=True)
        pendente = {"id": ident, "formato": pecas["formato"], "linha": item["_linha"],
                    "legenda": item.get("legenda_instagram", ""), "legenda_fb": legenda_fb(item),
                    "tentativas": 0, "publicado": estado["itens"].get(ident, {}).get("publicado", {})}
        if not pendente["legenda"].strip():
            raise RuntimeError("A coluna legenda_instagram está vazia.")
        if pecas["formato"] == "reel":
            drive_baixar(pecas["video"]["id"], PASTA_TMP / ident / "reel.mp4")
            pendente["video"] = str((PASTA_TMP / ident / "reel.mp4").relative_to(RAIZ))
            pendente["video_drive_id"] = pecas["video"]["id"]
            if pecas["capa"]:  # capa vai para o site (Instagram precisa de link público)
                tmp = drive_baixar(pecas["capa"]["id"], PASTA_TMP / ident / "capa_original")
                Image.open(tmp).convert("RGB").save(pasta_pub / "capa.jpg", "JPEG", quality=90)
                pendente["capa"] = f"docs/fila/{ident}/capa.jpg"
        else:
            pendente["slides"] = []
            for n, s in enumerate(pecas["slides"], start=1):
                tmp = drive_baixar(s["id"], PASTA_TMP / ident / f"slide_{n}")
                img = Image.open(tmp).convert("RGB")
                img.save(pasta_pub / f"{n}.jpg", "JPEG", quality=92)
                pendente["slides"].append(f"docs/fila/{ident}/{n}.jpg")
        estado["pendente"] = pendente
        salvar_estado(estado)
        log(f"{ident} pronto para publicar.")
    except Exception as e:
        log(f"ERRO preparando {ident}: {e}")
        planilha.atualizar(item, status="erro", log_robo=f"{agora():%d/%m %H:%M} {e}"[:500])


def publicar():
    estado = carregar_estado()
    p = estado.get("pendente")
    if not p:
        log("Nada da fila para publicar.")
        return
    raw, _ = urls_publicas()
    ident = p["id"]
    erros = []
    publicado = p.setdefault("publicado", {})

    if DRY_RUN:
        log(f"[SIMULAÇÃO] Publicaria {ident} ({p['formato']}) no Instagram e no Facebook.")
        log("[SIMULAÇÃO] Legenda:\n" + p["legenda"])
        estado["pendente"] = None
        salvar_estado(estado)
        return

    urls_slides = [f"{raw}/{s}" for s in p.get("slides", [])]
    url_capa = f"{raw}/{p['capa']}" if p.get("capa") else None
    video = RAIZ / p["video"] if p.get("video") else None
    if video and not video.exists():  # nova tentativa em outra execução: baixa de novo
        drive_baixar(p["video_drive_id"], video)

    if CONFIG["instagram"]["ativo"] and "instagram" not in publicado:
        try:
            publicado["instagram"] = ig_reel(video, p["legenda"], url_capa) if p["formato"] == "reel" \
                else ig_carrossel(urls_slides, p["legenda"])
            log(f"Instagram OK: {publicado['instagram']}")
        except Exception as e:
            erros.append(f"Instagram: {e}")
            log(f"ERRO Instagram: {e}")

    if CONFIG.get("facebook", {}).get("ativo") and "facebook" not in publicado:
        try:
            publicado["facebook"] = fb_reel(video, p["legenda_fb"]) if p["formato"] == "reel" \
                else fb_carrossel(urls_slides, p["legenda_fb"])
            log(f"Facebook OK: {publicado['facebook']}")
        except Exception as e:
            erros.append(f"Facebook: {e}")
            log(f"ERRO Facebook: {e}")

    p["tentativas"] = p.get("tentativas", 0) + 1
    estado["itens"].setdefault(ident, {})["publicado"] = publicado
    planilha = Planilha()
    item = planilha.por_id(ident)
    if not erros:
        if item:
            planilha.atualizar(item, status="postado", postado_em=f"{agora():%d/%m/%Y %H:%M}",
                               log_robo=" | ".join(f"{k}: {v}" for k, v in publicado.items()))
        estado["pendente"] = None
    elif p["tentativas"] >= MAX_TENTATIVAS:
        log("Desistindo depois de 3 tentativas.")
        if item:
            planilha.atualizar(item, status="erro", log_robo=f"{agora():%d/%m %H:%M} " + " | ".join(erros)[:450])
        estado["pendente"] = None
    else:
        log("Vou tentar de novo na próxima execução (só nas redes que falharam).")
    salvar_estado(estado)
    if estado["pendente"] is None:
        shutil.rmtree(PASTA_TMP / ident, ignore_errors=True)


def entregar():
    """Sobe para o Drive o que estiver em entregas/<ID>/ e marca 'aguardando aprovação'."""
    if not PASTA_ENTREGAS.exists():
        log("Nenhuma entrega.")
        return
    planilha = Planilha()
    for pasta in sorted(p for p in PASTA_ENTREGAS.iterdir() if p.is_dir()):
        item = planilha.por_id(pasta.name)
        if not item:
            log(f"Entrega {pasta.name}: não achei esse id na planilha, pulando.")
            continue
        pasta_id = id_do_link(item.get("pasta_midia"))
        if not pasta_id:
            log(f"Entrega {pasta.name}: a coluna pasta_midia está vazia, pulando.")
            continue
        arquivos = sorted(f for f in pasta.iterdir() if f.is_file() and not f.name.startswith("."))
        log(f"Entrega {pasta.name}: {len(arquivos)} arquivo(s)")
        peca = None
        slides = [f for f in arquivos if re.match(r"\d+\.(jpe?g|png)$", f.name, re.I)]
        if slides:  # carrossel: slides vão para a subpasta "Carrossel final"
            sub = drive_pasta("Carrossel final", pasta_id)
            for f in slides:
                drive_subir(f, sub["id"])
            peca = sub.get("webViewLink") or f"https://drive.google.com/drive/folders/{sub['id']}"
        for f in arquivos:
            if f in slides:
                continue
            enviado = drive_subir(f, pasta_id)
            log(f"  {f.name} -> Drive")
            if f.suffix.lower() == ".mp4" and "shopee" not in f.name.lower() and not peca:
                peca = enviado.get("webViewLink")
        campos = {"status": "aguardando aprovação", "log_robo": f"{agora():%d/%m %H:%M} peça entregue pelo Claude"}
        if peca:
            campos["peca_final"] = peca
        planilha.atualizar(item, **campos)
        shutil.rmtree(pasta)
        log(f"Entrega {pasta.name} concluída.")


def verificar():
    planilha = Planilha()
    log(f"Planilha OK, aba '{planilha.aba}', {len(planilha.linhas)} item(ns).")
    log("Colunas: " + ", ".join(c for c in planilha.cabecalho if c))
    for item in planilha.linhas:
        log(f"  {item['id']}: status='{item.get('status')}' formato='{item.get('formato')}' - {item.get('nome_curto')}")
    aprovado = primeiro_aprovado(planilha, carregar_estado())
    log(f"Próximo a publicar: {aprovado['id'] if aprovado else 'nenhum'}")
    if aprovado:
        pecas = pecas_do_item(aprovado)
        if pecas["formato"] == "reel":
            log(f"  vídeo: {pecas['video']['name']} | capa: {pecas['capa']['name'] if pecas['capa'] else 'sem capa'}")
        else:
            log("  slides: " + ", ".join(s["name"] for s in pecas["slides"]))


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else "verificar"
    {"verificar": verificar, "preparar": preparar, "publicar": publicar, "entregar": entregar}[comando]()
