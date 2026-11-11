"""
Radar dos Achados: arte nova do post anotado (aprovada em 09/11/2026).

1080x1350, foto do produto ocupando ~2/3, manchete Poppins + Caveat,
3 balões do kit da Karine com frases do banco (sem IA), rabiscos sorteados
e cores escolhidas para não brigar com as cores do produto.

Uso pelo radar.py:
    from arte import gerar_arte_anotada
    gerar_arte_anotada(p, destino, CONFIG["arte"])
"""
import colorsys
import io
import json
import random
import re
import unicodedata
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

RAIZ = Path(__file__).resolve().parent
PASTA_KIT = RAIZ / "assets" / "kit"
PASTA_FONTES = RAIZ / "assets" / "fontes"
ARQ_HISTORICO = RAIZ / "data" / "arte_historico.json"
UA = {"User-Agent": "Mozilla/5.0 (RadarDosAchados)"}

W, H = 1080, 1350
FOTO = (40, 258, 1040, 1236)  # caixa da foto: 1000 x 978 (~2/3 da arte)

# ------------------------------------------------------------------ paleta
TEXTO = (59, 42, 38)        # #3B2A26 marrom escuro (nunca preto)
TEXTO_2 = (122, 90, 80)     # #7A5A50
FUNDOS = {                  # nome: (cor, matiz principal ou None se neutro)
    "pessego": ((255, 227, 214), 18),
    "amarelo": ((255, 241, 201), 45),
    "rosado": ((251, 225, 228), 352),
    "creme": ((251, 243, 232), None),
}
DESTAQUES = {
    "coral": ((255, 132, 104), 11),
    "amarelo": ((247, 215, 116), 45),
    "rosa": ((244, 175, 184), 352),
    "salvia": ((157, 187, 147), 105),
}
FAMILIA_FUNDO = {"pessego": "coral", "amarelo": "amarelo", "rosado": "rosa", "creme": None}

# ------------------------------------------------------------------ kit
# faixa = estica na horizontal (1 linha); bloco = formato mais redondo (2 linhas)
BALOES = {
    "Bubble1": ("faixa", 46, 26), "Bubble2": ("faixa", 40, 26), "Bubble3": ("faixa", 56, 24),
    "Bubble4": ("faixa", 44, 26), "Bubble5": ("faixa", 44, 26), "Bubble6": ("faixa", 44, 26),
    "Bubble8": ("faixa", 52, 30), "Bubble11": ("faixa", 100, 22), "Bubble12": ("faixa", 44, 26),
    "Bubble15": ("faixa", 44, 24), "Bubble16": ("faixa", 44, 26), "Bubble17": ("faixa", 44, 28),
    "Bubble19": ("faixa", 52, 32), "Bubble21": ("faixa", 52, 34),
    "Bubble9": ("bloco", 64, 52), "Bubble13": ("bloco", 64, 52), "Bubble14": ("bloco", 70, 56),
    "Bubble18": ("bloco", 60, 48), "Bubble20": ("bloco", 56, 44),
}
PINCEIS_MANCHETE = ["Bubble1", "Bubble4", "Bubble5", "Bubble6", "Bubble2"]
RABISCOS_LINHA = ["estrela7", "estrela8", "estrela9", "estrelas10", "estrelas12", "estrelas13",
                  "coracao15", "coracao16", "constelacao6"]
RABISCOS_CHEIOS = ["estrela14", "coracao17"]
SETAS = ["Arrow7", "arrow5"]  # apontam para a direita


# ------------------------------------------------------------------ util
def _fonte(nome, tam, peso=None):
    f = ImageFont.truetype(str(PASTA_FONTES / nome), tam)
    if peso:
        try:
            f.set_variation_by_axes([peso])
        except Exception:
            pass
    return f


def poppins(tam, peso="ExtraBold"):
    return _fonte(f"Poppins-{peso}.ttf", tam)


def caveat(tam, peso=700):
    return _fonte("Caveat.ttf", tam, peso)


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _tem_palavra(texto, termo):
    return re.search(r"(?<![a-z0-9])" + re.escape(_sem_acento(termo)) + r"(?![a-z0-9])", _sem_acento(texto)) is not None


def _dist_matiz(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def carregar_historico():
    try:
        return json.loads(ARQ_HISTORICO.read_text(encoding="utf-8"))
    except Exception:
        return []


def salvar_historico(hist):
    ARQ_HISTORICO.parent.mkdir(parents=True, exist_ok=True)
    ARQ_HISTORICO.write_text(json.dumps(hist[-12:], ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ kit: carregar e recolorir
_cache = {}


def elemento(nome):
    if nome not in _cache:
        im = Image.open(PASTA_KIT / f"{nome}.png").convert("RGBA")
        bb = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
        _cache[nome] = im.crop(bb) if bb else im
    return _cache[nome]


def recolorir(im, cor, cor_contorno=TEXTO):
    """Peças só pretas viram a cor pedida. Peças com contorno + preenchimento
    (ex.: Bubble14, estrela14) ficam com contorno marrom e preenchimento na cor."""
    rgb = im.convert("RGB")
    alfa = im.getchannel("A")
    lum = rgb.convert("L")
    visiveis = [l for l, a in zip(lum.getdata(), alfa.getdata()) if a > 40]
    escuros = sum(1 for l in visiveis if l < 90) / max(1, len(visiveis))
    cheio = Image.new("RGB", im.size, cor)
    if escuros > 0.85:  # peça monocromática
        out = cheio
    else:
        mascara_escura = lum.point(lambda l: 255 if l < 90 else 0)
        out = Image.composite(Image.new("RGB", im.size, cor_contorno), cheio, mascara_escura)
    out = out.convert("RGBA")
    out.putalpha(alfa)
    return out


def rotacionar(im, graus):
    return im.rotate(graus, resample=Image.BICUBIC, expand=True)


# ------------------------------------------------------------------ foto e cores do produto
def baixar_foto(p):
    if p.get("imagem_local"):
        return Image.open(p["imagem_local"]).convert("RGB")
    r = requests.get(p["imagem"], headers=UA, timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def matizes_do_produto(foto):
    """Matizes (0-360) que dominam o produto. Ignora branco/cinza/preto."""
    pq = foto.copy()
    pq.thumbnail((120, 120))
    contagem = [0] * 36
    total = 0
    for r, g, b in pq.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if v > 0.93 and s < 0.10:  # fundo branco
            continue
        total += 1
        if s >= 0.22 and v >= 0.22:
            contagem[int(h * 36) % 36] += 1
    if not total:
        return []
    matizes = []
    for i, c in sorted(enumerate(contagem), key=lambda x: -x[1]):
        if c / total >= 0.12:
            matizes.append(i * 10 + 5)
    return matizes[:3]


def escolher_cores(matizes, hist):
    def livre(m):
        return m is None or all(_dist_matiz(m, x) > 30 for x in matizes)

    ultimo_fundo = hist[-1]["fundo"] if hist else None
    fundos = [n for n, (_, m) in FUNDOS.items() if livre(m)] or ["creme"]
    opcoes = [n for n in fundos if n != ultimo_fundo] or fundos
    fundo = random.choice(opcoes)
    destaques = [n for n, (_, m) in DESTAQUES.items() if livre(m) and n != FAMILIA_FUNDO[fundo]]
    if len(destaques) < 2:  # produto muito colorido: usa o que sobrou + marrom suave nos rabiscos
        destaques = destaques or [n for n in DESTAQUES if n != FAMILIA_FUNDO[fundo]][:1]
    random.shuffle(destaques)
    return fundo, destaques


# ------------------------------------------------------------------ textos
def descobrir_tema(p, cfg):
    # 1º a palavra usada na busca (mais confiável), depois o nome do produto
    for texto in (p.get("palavra", ""), p.get("nome", "")):
        for tema, t in cfg["temas"].items():
            if texto and any(_tem_palavra(texto, termo) for termo in t["termos"]):
                return tema
    cat = p.get("categoria")
    return cat if cat in cfg["temas"] else None


def escolher_textos(p, cfg, hist):
    tema = descobrir_tema(p, cfg)
    usadas = {f for h in hist[-6:] for f in h.get("frases", [])}
    manchetes_usadas = {tuple(h.get("manchete", [])) for h in hist[-4:]}

    manchetes = list(cfg["temas"][tema]["manchetes"]) if tema else []
    manchetes += cfg["manchetes_gerais"] if not manchetes or random.random() < 0.3 else []
    manchetes = [m for m in manchetes if tuple(m) not in manchetes_usadas] or manchetes
    manchete = random.choice(manchetes)

    especificas = [f for palavra, f in cfg["frases_por_palavra"].items() if _tem_palavra(p.get("nome", ""), palavra)]
    if tema:
        especificas += cfg["temas"][tema]["frases"]
    especificas = list(dict.fromkeys(especificas))
    frases = []
    candidatas = [f for f in especificas if f not in usadas] or especificas
    if candidatas:
        frases.append(random.choice(candidatas))
    genericas = [f for f in cfg["frases_genericas"] if f not in usadas and f not in frases]
    random.shuffle(genericas)
    frases += genericas[: 3 - len(frases)]
    return tema, manchete, random.choice(cfg["aberturas"]), frases


# ------------------------------------------------------------------ desenho: balão com texto
def quebrar_em_2(texto):
    palavras = texto.split()
    if len(palavras) < 2:
        return [texto]
    melhor = None
    for i in range(1, len(palavras)):
        a, b = " ".join(palavras[:i]), " ".join(palavras[i:])
        diff = abs(len(a) - len(b))
        if melhor is None or diff < melhor[0]:
            melhor = (diff, [a, b])
    return melhor[1]


def balao_com_texto(texto, nome, cor, tam_fonte=50):
    tipo, pad_x, pad_y = BALOES[nome]
    fnt = caveat(tam_fonte)
    linhas = quebrar_em_2(texto) if (tipo == "bloco" or len(texto) > 16) else [texto]
    medidas = [fnt.getbbox(l) for l in linhas]
    larg_txt = max(b[2] - b[0] for b in medidas)
    alt_linha = int(tam_fonte * 1.05)
    alt_txt = alt_linha * len(linhas)
    bw, bh = larg_txt + 2 * pad_x, alt_txt + 2 * pad_y
    if tipo == "bloco":  # mantém o formato arredondado
        bw = max(bw, int(bh * 1.25))
        bh = max(bh, int(bw / 1.9))
    forma = recolorir(elemento(nome), cor).resize((bw, bh), Image.LANCZOS)
    camada = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    camada.alpha_composite(forma)
    d = ImageDraw.Draw(camada)
    y = (bh - alt_txt) // 2 - int(tam_fonte * 0.12)
    for l in linhas:
        lw = fnt.getlength(l)
        d.text(((bw - lw) / 2, y), l, font=fnt, fill=TEXTO)
        y += alt_linha
    return camada


def colar_centro(base, camada, cx, cy):
    base.alpha_composite(camada, (int(cx - camada.width / 2), int(cy - camada.height / 2)))


# ------------------------------------------------------------------ desenho: arte completa
def mascara_produto(foto_box):
    """Onde está o produto dentro da caixa da foto (pixels que não são fundo claro)."""
    cinza = foto_box.convert("L")
    m = cinza.point(lambda v: 255 if v < 236 else 0).filter(ImageFilter.MaxFilter(9))
    return m


def ocupacao(mascara, caixa):
    x0, y0, x1, y1 = [int(v) for v in caixa]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(mascara.width, x1), min(mascara.height, y1)
    if x1 <= x0 or y1 <= y0:
        return 1.0
    reg = mascara.crop((x0, y0, x1, y1))
    hist = reg.histogram()
    return hist[255] / max(1, (x1 - x0) * (y1 - y0))


def gerar_arte_anotada(p, destino, cfg, semente=None):
    if semente is not None:
        random.seed(semente)
    hist = carregar_historico()
    foto = baixar_foto(p)
    fundo_nome, destaques = escolher_cores(matizes_do_produto(foto), hist)
    cor_fundo = FUNDOS[fundo_nome][0]
    cor = lambda i: DESTAQUES[destaques[i % len(destaques)]][0]
    tema, manchete, abertura, frases = escolher_textos(p, cfg, hist)

    img = Image.new("RGBA", (W, H), cor_fundo + (255,))
    d = ImageDraw.Draw(img)

    # --- topo: @ pequeno
    marca = "@RADARDOSACHADOSOF"
    f_marca = poppins(22, "SemiBold")
    d.text((W / 2, 34), marca, font=f_marca, fill=TEXTO_2, anchor="mm")

    # --- frase manuscrita
    f_ab = caveat(54)
    cam = Image.new("RGBA", (700, 90), (0, 0, 0, 0))
    ImageDraw.Draw(cam).text((350, 45), abertura, font=f_ab, fill=TEXTO, anchor="mm")
    colar_centro(img, rotacionar(cam, random.choice([-4, -3, 3])), W / 2, 86)

    # --- manchete: linha fina + palavra forte sobre pincelada
    fina, forte = manchete
    tam_forte = 82
    f_forte = poppins(tam_forte, "ExtraBold")
    while f_forte.getlength(forte) > 900 and tam_forte > 50:
        tam_forte -= 4
        f_forte = poppins(tam_forte, "ExtraBold")
    f_fina = poppins(40, "Regular")
    d.text((W / 2, 146), fina, font=f_fina, fill=TEXTO, anchor="mm")
    lw = f_forte.getlength(forte)
    pincel = recolorir(elemento(random.choice(PINCEIS_MANCHETE)), cor(0))
    pincel = pincel.resize((int(lw + 70), int(tam_forte * 0.78)), Image.LANCZOS)
    colar_centro(img, rotacionar(pincel, random.uniform(-1.5, 1.5)), W / 2, 214)
    d = ImageDraw.Draw(img)
    d.text((W / 2, 206), forte, font=f_forte, fill=TEXTO, anchor="mm")

    # --- foto grande em quadro branco arredondado
    fx0, fy0, fx1, fy1 = FOTO
    bw, bh = fx1 - fx0, fy1 - fy0
    quadro = Image.new("RGB", (bw, bh), (255, 255, 255))
    prod = ImageOps.contain(foto, (bw - 40, bh - 40), Image.LANCZOS)
    quadro.paste(prod, ((bw - prod.width) // 2, (bh - prod.height) // 2))
    masc_quadro = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(masc_quadro).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=46, fill=255)
    sombra = Image.new("RGBA", (bw + 40, bh + 40), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle([20, 26, bw + 20, bh + 26], radius=46, fill=(59, 42, 38, 40))
    img.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(12)), (fx0 - 20, fy0 - 20))
    img.paste(quadro, (fx0, fy0), masc_quadro)
    prod_mask = Image.new("L", (W, H), 0)
    prod_mask.paste(mascara_produto(quadro), (fx0, fy0), masc_quadro)

    # --- 3 balões nos cantos/laterais com menos produto
    vagas = [  # (cx, cy, lado) dentro da foto, encostando nas bordas
        (fx0 + 200, fy0 + 95, "esq"), (fx1 - 200, fy0 + 95, "dir"),
        (fx0 + 190, fy0 + bh * 0.52, "esq"), (fx1 - 190, fy0 + bh * 0.52, "dir"),
        (fx0 + 210, fy1 - 95, "esq"), (fx1 - 210, fy1 - 95, "dir"),
    ]
    nomes_usados = {n for h in hist[-3:] for n in h.get("baloes", [])}
    opcoes_balao = [n for n in BALOES if n not in nomes_usados] or list(BALOES)
    random.shuffle(opcoes_balao)
    camadas = []
    for i, frase in enumerate(frases):
        nome = opcoes_balao[i % len(opcoes_balao)]
        camadas.append((nome, rotacionar(balao_com_texto(frase, nome, cor(i)), random.uniform(-5, 5))))

    escolhidas = []
    for nome, cam in camadas:
        melhor = None
        for vx, vy, lado in vagas:
            if any(abs(vy - ey) < 150 and lado == el for ex, ey, el in escolhidas):
                continue
            if any(abs(vx - ex) < 300 and abs(vy - ey) < 200 for ex, ey, el in escolhidas):
                continue
            cx = fx0 + cam.width / 2 - 18 if lado == "esq" else fx1 - cam.width / 2 + 18
            caixa = (cx - cam.width / 2, vy - cam.height / 2, cx + cam.width / 2, vy + cam.height / 2)
            nota = ocupacao(prod_mask, caixa)
            lados = [e[2] for e in escolhidas]
            if lados.count(lado) >= 2:
                nota += 0.5  # equilibra esquerda/direita
            if melhor is None or nota < melhor[0]:
                melhor = (nota, cx, vy, lado)
        _, cx, cy, lado = melhor
        colar_centro(img, cam, cx, cy)
        escolhidas.append((cx, cy, lado))

    # --- seta saindo de um balão lateral em direção ao centro
    meio = [e for e in escolhidas if abs(e[1] - (fy0 + bh * 0.52)) < 10] or escolhidas
    ex, ey, lado = random.choice(meio)
    nome_seta = random.choice(SETAS)
    seta = recolorir(elemento(nome_seta), cor(1) if nome_seta == "Arrow7" else TEXTO)
    seta.thumbnail((170, 120))
    if lado == "dir":
        seta = ImageOps.mirror(seta)
    seta = rotacionar(seta, random.uniform(-12, 12))
    sx = ex + (230 if lado == "esq" else -230)
    sy = ey + random.choice([-90, 90])
    sy = min(max(sy, fy0 + 90), fy1 - 90)  # seta sempre dentro da foto
    colar_centro(img, seta, sx, sy)

    # --- rabiscos: ao lado da manchete e nos cantos da foto
    vagas_rabisco = [(110, 150, 95), (W - 110, 160, 95), (fx1 - 30, fy0 + 10, 110), (fx0 + 20, fy1 - 10, 100)]
    random.shuffle(vagas_rabisco)
    rabiscos = random.sample(RABISCOS_LINHA, 2) + [random.choice(RABISCOS_CHEIOS)]
    for (rx, ry, tam), nome in zip(vagas_rabisco, rabiscos):
        cor_r = cor(2) if nome in RABISCOS_CHEIOS else random.choice([TEXTO, cor(1), cor(0)])
        r = recolorir(elemento(nome), cor_r)
        r.thumbnail((tam, tam))
        colar_centro(img, rotacionar(r, random.uniform(-15, 15)), rx, ry)

    # --- rodapé
    d = ImageDraw.Draw(img)
    f1 = poppins(30, "SemiBold")
    f1b = poppins(30, "ExtraBold")
    partes = [("comente", f1), ("ACHADO", f1b), ("que eu te mando o link", f1)]
    gap = 22
    total = sum(f.getlength(t) for t, f in partes) + 2 * gap
    x = (W - total) / 2
    y = 1272
    for t, f in partes:
        tw = f.getlength(t)
        if t == "ACHADO":
            d.rounded_rectangle([x - 12, y - 2, x + tw + 12, y + 44], radius=12, fill=cor(0))
        d.text((x, y), t, font=f, fill=TEXTO)
        x += tw + gap
    f2 = poppins(19, "Medium")
    d.text((W / 2, 1330), f"#PUBLI · LINK DE AFILIADO · ACHADO Nº {p.get('numero', '')}", font=f2, fill=TEXTO_2, anchor="mm")

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(destino, "JPEG", quality=92)

    hist.append({"fundo": fundo_nome, "destaques": destaques, "baloes": [n for n, _ in camadas],
                 "frases": frases, "manchete": manchete, "tema": tema})
    salvar_historico(hist)
    return {"fundo": fundo_nome, "destaques": destaques, "tema": tema, "frases": frases, "manchete": manchete}
