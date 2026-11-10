#!/usr/bin/env python3
"""
Radar de tendências do Radar dos Achados.

Comandos:
  python tendencias.py coletar     -> busca as palavras-chave na Shopee (mais vendidos) e
                                      anota quantas vendas cada produto tem hoje (termômetro)
  python tendencias.py relatorio   -> calcula quem mais vendeu nos últimos 7 dias e escreve
                                      a aba "Tendências" da planilha da fila curada
  python tendencias.py ver         -> mostra o ranking no log, sem mexer na planilha

O termômetro fica em data/termometro.json. A cada coleta, cada produto ganha um registro
{data: vendas}. Comparando a contagem de hoje com a de ~7 dias atrás, sabemos quantas
vendas ele fez na semana, mesmo que o total seja antigo.

Secrets: SHOPEE_APP_ID, SHOPEE_SECRET (coleta) e GOOGLE_* (relatório). DRY_RUN=1 só mostra.
"""
import json
import math
import re
import sys
import time
from datetime import date, timedelta

from radar import CONFIG, DRY_RUN, RAIZ, agora, brl, categoria_de, log, normalizar, shopee

CFG = CONFIG.get("tendencias", {})
ARQ = RAIZ / "data" / "termometro.json"
DIAS_GUARDADOS = CFG.get("dias_guardados", 21)      # histórico por produto
PRODUTOS_POR_PALAVRA = CFG.get("produtos_por_palavra", 50)
PAGINAS = CFG.get("paginas", 2)                      # 2 páginas x 50 = 100 por palavra
TOP = CFG.get("itens_no_relatorio", 40)
ABA = CFG.get("aba", "Tendências")
ABA_PALAVRAS = CFG.get("aba_palavras", "Tendências - palavras")
OPCOES_USO = ["post único", "carrossel", "fila curada", "virar palavra-chave", "ignorar"]


# ---------------------------------------------------------------- termômetro
def carregar():
    if ARQ.exists():
        return json.loads(ARQ.read_text(encoding="utf-8"))
    return {"produtos": {}}


def salvar(dados):
    ARQ.parent.mkdir(parents=True, exist_ok=True)
    # compacto de propósito: o arquivo é atualizado todo dia
    ARQ.write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def termos_para_coletar():
    termos = list(CONFIG.get("palavras_chave", [])) + list(CFG.get("termos_extra", []))
    vistos, saida = set(), []
    for t in termos:
        if t.strip().lower() not in vistos:
            vistos.add(t.strip().lower())
            saida.append(t.strip())
    return saida


def buscar(palavra, pagina):
    q = (
        "{productOfferV2(keyword:%s, sortType:2, page:%d, limit:%d){nodes{"
        "itemId productName priceMin priceMax priceDiscountRate imageUrl offerLink "
        "productLink sales ratingStar shopName commissionRate}}}"
        % (json.dumps(palavra, ensure_ascii=False), pagina, PRODUTOS_POR_PALAVRA)
    )
    # A API da Shopee às vezes devolve "System Error [10000]" quando recebe muitas
    # buscas seguidas; esperar um pouco e tentar de novo costuma resolver.
    for tentativa in range(4):
        try:
            nos = shopee(q)["productOfferV2"]["nodes"] or []
            time.sleep(3)
            return nos
        except RuntimeError as e:
            if "10000" not in str(e) or tentativa == 3:
                raise
            time.sleep(10 * (tentativa + 1))


def coletar():
    dados = carregar()
    hoje = agora().date().isoformat()
    total = 0
    for palavra in termos_para_coletar():
        for pagina in range(1, PAGINAS + 1):
            try:
                nos = buscar(palavra, pagina)
            except Exception as e:  # uma palavra com erro não derruba a coleta
                log(f"Erro em '{palavra}' (página {pagina}): {e}")
                break
            for bruto in nos:
                p = normalizar(bruto)
                p["palavra"] = palavra
                reg = dados["produtos"].setdefault(p["id"], {"hist": {}, "visto_em": hoje})
                reg.update(
                    nome=p["nome"], palavra=reg.get("palavra") or palavra, categoria=categoria_de(p),
                    preco=p["preco"], desconto=p["desconto"], nota=p["nota"], imagem=p["imagem"],
                    link=p["link"] or p["link_produto"], loja=p["loja"],
                    comissao=float(bruto.get("commissionRate") or 0), visto_em=hoje,
                )
                reg["hist"][hoje] = p["vendas"]
                total += 1
            log(f"'{palavra}' p.{pagina}: {len(nos)} produtos")
            if len(nos) < PRODUTOS_POR_PALAVRA:
                break

    # limpeza: histórico curto e produtos que sumiram das buscas
    corte = (agora().date() - timedelta(days=DIAS_GUARDADOS)).isoformat()
    for pid in list(dados["produtos"]):
        reg = dados["produtos"][pid]
        reg["hist"] = {d: v for d, v in reg["hist"].items() if d >= corte}
        if not reg["hist"]:
            del dados["produtos"][pid]
    dados["ultima_coleta"] = hoje
    salvar(dados)
    log(f"Termômetro atualizado: {total} leituras, {len(dados['produtos'])} produtos acompanhados.")


# --------------------------------------------------------------------- cálculo
def ritmo(reg, hoje=None, janela=7, minimo=3):
    """Vendas por dia na janela mais recente (~7 dias) e na janela anterior.

    Usa a leitura mais antiga dentro da janela que tenha pelo menos `minimo` dias,
    para o relatório já funcionar na primeira semana.
    Devolve (vendas_por_dia, vendas_por_dia_semana_anterior ou None, dias_medidos) ou None.
    """
    hoje = hoje or agora().date()
    pontos = sorted((date.fromisoformat(d), v) for d, v in reg["hist"].items())
    if len(pontos) < 2:
        return None
    d_fim, v_fim = pontos[-1]
    if (hoje - d_fim).days > 2:  # sumiu das buscas recentes
        return None
    base = [(d, v) for d, v in pontos if minimo <= (d_fim - d).days <= janela]
    if not base:
        return None
    d_ini, v_ini = base[0]
    dias = (d_fim - d_ini).days
    por_dia = max(v_fim - v_ini, 0) / dias

    anterior = None
    antes = [(d, v) for d, v in pontos if minimo <= (d_ini - d).days <= janela]
    if antes:
        d0, v0 = antes[0]
        anterior = max(v_ini - v0, 0) / (d_ini - d0).days
    return por_dia, anterior, dias


def proibido(nome):
    nome = nome.lower()
    return any(x in nome for x in CONFIG["filtros"]["palavras_proibidas"])


def ranking(dados=None):
    dados = dados or carregar()
    f = CONFIG["filtros"]
    linhas = []
    for pid, reg in dados["produtos"].items():
        r = ritmo(reg)
        if not r:
            continue
        por_dia, anterior, dias = r
        semana = round(por_dia * 7)
        if semana <= 0 or proibido(reg["nome"]):
            continue
        if reg["nota"] and reg["nota"] < f["nota_minima"]:
            continue
        if not (f["preco_minimo"] <= reg["preco"] <= f["preco_maximo"]):
            continue
        crescimento = None
        if anterior is not None:
            crescimento = round((por_dia / anterior - 1) * 100) if anterior > 0 else 999
        ganho = reg["preco"] * reg.get("comissao", 0)
        # pontuação: ritmo de vendas (escala log), aceleração e o quanto rende por venda
        pontos = math.log10(semana + 1) * 40
        if crescimento is not None:
            pontos += max(min(crescimento, 300), -50) / 10
        pontos += min(ganho, 20)
        linhas.append({
            "id": pid, **{k: reg.get(k) for k in ("nome", "palavra", "categoria", "preco", "desconto",
                                                   "nota", "imagem", "link", "comissao", "loja")},
            "total": max(reg["hist"].values()), "semana": semana, "crescimento": crescimento,
            "ganho": round(ganho, 2), "dias": dias, "pontos": round(pontos, 1),
        })
    linhas.sort(key=lambda l: l["pontos"], reverse=True)
    return linhas


def resumo_palavras(linhas):
    por = {}
    for l in linhas:
        p = por.setdefault(l["palavra"], {"palavra": l["palavra"], "semana": 0, "produtos": 0, "acelerando": 0})
        p["semana"] += l["semana"]
        p["produtos"] += 1
        if (l["crescimento"] or 0) >= 20:
            p["acelerando"] += 1
    return sorted(por.values(), key=lambda p: p["semana"], reverse=True)


def bonus_tendencia(pid, dados):
    """Usado pelo radar.py: pontos extras para produtos que estão vendendo rápido."""
    reg = dados.get("produtos", {}).get(pid)
    r = ritmo(reg) if reg else None
    if not r:
        return 0
    por_dia, anterior, _ = r
    bonus = math.log10(por_dia * 7 + 1) * 8
    if anterior and por_dia > anterior * 1.2:
        bonus += 5
    return round(bonus, 1)


# --------------------------------------------------------------------- saída
def ver():
    linhas = ranking()
    if not linhas:
        log("Ainda não há histórico suficiente (precisa de pelo menos 3 dias de coleta).")
        return
    for i, l in enumerate(linhas[:TOP], 1):
        cresc = "novo" if l["crescimento"] is None else f"{l['crescimento']:+d}%"
        print(f"{i:>2}. {l['semana']:>6} vendas/sem  {cresc:>6}  {brl(l['preco']):>11}  "
              f"{l['comissao'] * 100:>4.1f}%  [{l['palavra']}] {l['nome'][:60]}")
    print("\nPalavras-chave mais quentes:")
    for p in resumo_palavras(linhas)[:10]:
        print(f"  {p['palavra']:<22} {p['semana']:>7} vendas/sem  ({p['acelerando']} acelerando)")


def _celula(v):
    return "" if v is None else v


def relatorio():
    linhas = ranking()
    if not linhas:
        log("Ainda não há histórico suficiente para o relatório (mínimo de 3 dias de coleta).")
        return
    top = linhas[:TOP]
    hoje = agora().strftime("%d/%m/%Y")
    if DRY_RUN:
        log("[SIMULAÇÃO] Escreveria a aba de tendências:")
        ver()
        return

    from fila import Planilha, google, requests  # só aqui: precisa dos secrets do Google

    planilha = Planilha()
    base = planilha.base
    abas = {s["properties"]["title"]: s["properties"]["sheetId"]
            for s in google("GET", base, params={"fields": "sheets.properties"}).json()["sheets"]}

    def garantir_aba(nome):
        if nome not in abas:
            r = google("POST", f"{base}:batchUpdate",
                       json={"requests": [{"addSheet": {"properties": {"title": nome}}}]}).json()
            abas[nome] = r["replies"][0]["addSheet"]["properties"]["sheetId"]
        return abas[nome]

    def ler(nome):
        intervalo = requests.utils.quote(f"'{nome}'!A1:Z500")
        return google("GET", f"{base}/values/{intervalo}").json().get("values", [])

    def escrever(nome, valores):
        intervalo = requests.utils.quote(f"'{nome}'!A1:Z500")
        google("POST", f"{base}/values/{intervalo}:clear", json={})
        google("PUT", f"{base}/values/{requests.utils.quote(chr(39) + nome + chr(39) + '!A1')}",
               params={"valueInputOption": "USER_ENTERED"}, json={"values": valores})

    # guarda o que a Karine já marcou, pelo id do produto
    aba_id = garantir_aba(ABA)
    antigas = ler(ABA)
    marcas = {}
    if len(antigas) > 2:
        cab = [c.strip().lower() for c in antigas[1]]
        if "id" in cab and "usar como" in cab:
            i_id, i_uso = cab.index("id"), cab.index("usar como")
            i_obs = cab.index("observação") if "observação" in cab else None
            for l in antigas[2:]:
                l = l + [""] * (len(cab) - len(l))
                if l[i_id] and (l[i_uso] or (i_obs is not None and l[i_obs])):
                    marcas[l[i_id]] = (l[i_uso], l[i_obs] if i_obs is not None else "")

    cabecalho = ["#", "foto", "produto", "categoria", "palavra-chave", "vendas na semana", "crescimento",
                 "vendas no total", "preço", "comissão", "ganho por venda", "nota", "link", "usar como",
                 "observação", "id"]
    valores = [[f"Tendências da semana · atualizado em {hoje} · vendas medidas em {top[0]['dias']} dias "
                f"(contagem da Shopee; 'novo' = ainda sem semana anterior para comparar)"], cabecalho]
    ids_no_top = set()
    for i, l in enumerate(top, 1):
        uso, obs = marcas.get(l["id"], ("", ""))
        ids_no_top.add(l["id"])
        valores.append([
            i, f'=IMAGE("{l["imagem"]}")' if l["imagem"] else "", l["nome"], l["categoria"], l["palavra"],
            l["semana"], "novo" if l["crescimento"] is None else f"{l['crescimento']:+d}%",
            l["total"], l["preco"], f"{l['comissao'] * 100:.1f}%", l["ganho"], l["nota"], l["link"],
            uso, obs, l["id"],
        ])
    antigos_marcados = [pid for pid in marcas if pid not in ids_no_top]
    if antigos_marcados:
        valores.append([])
        valores.append(["Marcados em semanas anteriores (saíram do ranking)"])
        termo = carregar()["produtos"]
        for pid in antigos_marcados:
            reg = termo.get(pid, {})
            uso, obs = marcas[pid]
            valores.append(["", f'=IMAGE("{reg["imagem"]}")' if reg.get("imagem") else "",
                            reg.get("nome", ""), reg.get("categoria", ""), reg.get("palavra", ""),
                            "", "", "", reg.get("preco", ""), "", "", reg.get("nota", ""),
                            reg.get("link", ""), uso, obs, pid])
    escrever(ABA, valores)

    # formatação: cabeçalho fixo, fotos maiores e lista suspensa em "usar como"
    n = len(top)
    google("POST", f"{base}:batchUpdate", json={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": aba_id, "gridProperties": {"frozenRowCount": 2}},
                                   "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": aba_id, "startRowIndex": 1, "endRowIndex": 2},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                       "backgroundColor": {"red": 1, "green": .89, "blue": .84}}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"updateDimensionProperties": {"range": {"sheetId": aba_id, "dimension": "ROWS",
                                                 "startIndex": 2, "endIndex": 2 + n},
                                       "properties": {"pixelSize": 70}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": aba_id, "dimension": "COLUMNS",
                                                 "startIndex": 1, "endIndex": 2},
                                       "properties": {"pixelSize": 80}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": aba_id, "dimension": "COLUMNS",
                                                 "startIndex": 2, "endIndex": 3},
                                       "properties": {"pixelSize": 320}, "fields": "pixelSize"}},
        {"setDataValidation": {"range": {"sheetId": aba_id, "startRowIndex": 2, "endRowIndex": 2 + n,
                                         "startColumnIndex": 13, "endColumnIndex": 14},
                               "rule": {"condition": {"type": "ONE_OF_LIST",
                                                      "values": [{"userEnteredValue": o} for o in OPCOES_USO]},
                                        "showCustomUi": True, "strict": False}}},
    ]})

    # aba com as palavras-chave mais quentes
    garantir_aba(ABA_PALAVRAS)
    palavras = [[f"Palavras-chave mais quentes · {hoje}"],
                ["palavra-chave", "vendas na semana (top produtos)", "produtos no ranking", "acelerando (+20%)"]]
    for p in resumo_palavras(linhas):
        palavras.append([p["palavra"], p["semana"], p["produtos"], p["acelerando"]])
    escrever(ABA_PALAVRAS, palavras)
    log(f"Aba '{ABA}' atualizada com {n} produtos ({len(antigos_marcados)} marcados de semanas anteriores).")


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else "ver"
    {"coletar": coletar, "relatorio": relatorio, "ver": ver}[comando]()
