"""
Arte do post anotado do Radar dos Achados (visual aprovado em 09/11/2026).

- Paleta: coral, amarelo, rosa e sálvia, com fundos claros.
- As cores da arte nunca concorrem com a cor do produto: o robô lê a cor
  dominante da foto e só usa cores da paleta que contrastam com ela.
- A foto do produto ocupa a maior parte da arte (cartão branco no centro).
- Balões e rabiscos vêm do kit em assets/kit (PNGs pretos, recoloridos aqui).
- Frases dos balões e manchetes vêm do config.json (banco editável).
"""
import colorsys
import math
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

RAIZ = Path(__file__).resolve().parent
KIT = RAIZ / "assets" / "kit"
FONTES = RAIZ / "assets" / "fontes"
W, H = 1080, 1350

# ------------------------------------------------------------------ paleta
CORES = {
    "coral": (255, 132, 104),
    "amarelo": (247, 215, 116),
    "rosa": (244, 175, 184),
    "salvia": (157, 187, 147),
}
MATIZ = {"coral": 11, "amarelo": 45, "rosa": 353, "salvia": 105}  # em graus
FUNDOS = [  # (cor de fundo, cor da paleta "parente" que não pode conflitar)
    ((255, 227, 214), "coral"),    # pêssego
    ((255, 241, 201), "amarelo"),  # amarelo suave
    ((251, 225, 228), "rosa"),     # rosado
    ((251, 243, 232), None),       # creme (neutro)
]
TEXTO = (59, 42, 38)
TEXTO_2 = (122, 90, 80)

# ------------------------------------------------------------------- kit
# Área útil para o texto dentro de cada balão (fração da largura/altura) e ajuste do centro.
BALOES_LARGOS = {
    "bubble1": {"w": 0.66}, "bubble2": {"w": 0.76}, "bubble3": {"w": 0.74}, "bubble4": {"w": 0.78},
    "bubble5": {"w": 0.76}, "bubble6": {"w": 0.76}, "bubble8": {"w": 0.78}, "bubble12": {}, "bubble15": {},
    "bubble16": {"dx": 0.02}, "bubble17": {"w": 0.8},
    "bubble11": {"w": 0.58, "h": 0.5, "dy": -0.06},
}
BALOES_ALTOS = {
    "bubble9": {"w": 0.7, "h": 0.6}, "bubble13": {"w": 0.74, "h": 0.68},
    "bubble14": {"w": 0.7, "h": 0.64}, "bubble18": {"w": 0.74, "h": 0.74},
    "bubble19": {"w": 0.76, "h": 0.66}, "bubble20": {"w": 0.74, "h": 0.74},
    "bubble21": {"w": 0.78, "h": 0.66},
}
DESTAQUES = ["bubble1", "bubble2", "bubble4", "bubble5", "bubble6"]  # pinceladas da manchete
# Direção de cada seta (graus, do início para a ponta, eixo y para baixo).
SETAS = {"arrow3": -61, "arrow5": 52, "arrow7": 0}
RABISCOS_PEQUENOS = ["estrela7", "estrela8", "estrela9", "estrela14", "estrelas12", "coracao15", "coracao16", "coracao17", "risco4"]
RABISCOS_GRANDES = ["constelacao6", "estrelas10", "estrelas13", "gatinho11", "circulo2"]

_cache = {}


def fonte(nome, tamanho):
    chave = (nome, tamanho)
    if chave not in _cache:
        _cache[chave] = ImageFont.truetype(str(FONTES / f"{nome}.ttf"), tamanho)
    return _cache[chave]


def kit(nome):
    """Máscara (canal alfa) do elemento do kit."""
    chave = ("kit", nome)
    if chave not in _cache:
        _cache[chave] = Image.open(KIT / f"{nome}.png").convert("RGBA").getchannel("A")
    return _cache[chave]


def colorir(mascara, cor):
    img = Image.new("RGBA", mascara.size, cor + (255,))
    img.putalpha(mascara)
    return img


def elemento(nome, cor, largura=None, altura=None):
    m = kit(nome)
    if largura and not altura:
        altura = round(m.height * largura / m.width)
    elif altura and not largura:
        largura = round(m.width * altura / m.height)
    if largura:
        m = m.resize((max(1, int(largura)), max(1, int(altura))), Image.LANCZOS)
    return colorir(m, cor)


def colar(base, img, x, y):
    base.alpha_composite(img, (int(round(x)), int(round(y))))


# ------------------------------------------------------------ cor do produto
def fundo_branco(foto):
    """True se a borda da foto é quase toda branca (foto típica de catálogo)."""
    p = foto.convert("RGB").resize((120, 120))
    px = p.load()
    borda = [px[i, 0] for i in range(120)] + [px[i, 119] for i in range(120)]
    borda += [px[0, i] for i in range(120)] + [px[119, i] for i in range(120)]
    claros = sum(1 for r, g, b in borda if min(r, g, b) > 232)
    return claros / len(borda) > 0.85


def cortar_margem_branca(foto):
    """Remove as sobras brancas em volta do produto, para ele aparecer maior."""
    cinza = foto.convert("L").point(lambda v: 255 if v < 235 else 0)
    caixa = cinza.getbbox()
    if not caixa:
        return foto
    x0, y0, x1, y1 = caixa
    folga = int(max(x1 - x0, y1 - y0) * 0.04)
    return foto.crop((max(0, x0 - folga), max(0, y0 - folga), min(foto.width, x1 + folga), min(foto.height, y1 + folga)))


def distancia_matiz(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def pesos_de_matiz(foto):
    """Quanto de cada matiz (graus) aparece no produto. Em fotos de ambiente, olha só o centro."""
    img = foto.convert("RGB")
    if not fundo_branco(img):
        w, h = img.size
        img = img.crop((int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)))
    pesos = [0.0] * 36  # faixas de 10 graus
    total = 0
    for r, g, b in img.resize((80, 80)).getdata():
        if min(r, g, b) > 232:  # fundo branco
            continue
        total += 1
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.15 or v < 0.2:  # branco, preto e cinza não competem
            continue
        pesos[int(h * 36) % 36] += s * v
    return pesos, total


def cores_em_conflito(foto):
    """Cores da paleta que aparecem com força no produto."""
    pesos, total = pesos_de_matiz(foto)
    colorido = sum(pesos)
    if total == 0 or colorido / total < 0.06:
        return set()
    conflito = set()
    for nome, matiz in MATIZ.items():
        perto = sum(p for i, p in enumerate(pesos) if distancia_matiz(i * 10 + 5, matiz) <= 35)
        if perto / colorido > 0.18:
            conflito.add(nome)
    return conflito


def escolher_cores(foto):
    """Fundo e cores de destaque que não concorrem com o produto."""
    conflito = cores_em_conflito(foto)
    livres = [c for c in CORES if c not in conflito]
    if len(livres) < 2:  # produto muito colorido: usa as cores mais distantes
        pesos, _ = pesos_de_matiz(foto)
        def risco(c):
            return sum(p for i, p in enumerate(pesos) if distancia_matiz(i * 10 + 5, MATIZ[c]) <= 35)
        livres = sorted(CORES, key=risco)[:2]
    fundos = [f for f, parente in FUNDOS if parente is None or parente in livres]
    random.shuffle(livres)
    return random.choice(fundos), livres


# ------------------------------------------------------------------ textos
def _bate(termo, texto):
    return re.search(r"(?<![a-zà-ú])" + re.escape(termo.lower()) + r"(?![a-zà-ú])", texto) is not None


def escolher_frases(p, cfg, evitar=()):
    """1 frase específica (se a característica estiver no nome do produto) + genéricas, até 3."""
    banco = cfg.get("frases", {})
    nome = p.get("nome", "").lower()
    evitar = set(evitar)
    escolhidas = []

    especificas = [frase for termo, frase in banco.get("por_palavra", {}).items() if _bate(termo, nome)]
    especificas = [f for f in especificas if f not in evitar] or especificas
    if especificas:
        escolhidas.append(random.choice(especificas))

    do_tema = banco.get("por_categoria", {}).get(p.get("categoria", ""), [])
    do_tema = [f for f in do_tema if f not in evitar and f not in escolhidas]
    if do_tema and random.random() < 0.6:
        escolhidas.append(random.choice(do_tema))

    genericas = [f for grupo in banco.get("genericas", {}).values() for f in grupo]
    novas = [f for f in genericas if f not in evitar and f not in escolhidas]
    random.shuffle(novas)
    while len(escolhidas) < 3 and novas:
        escolhidas.append(novas.pop())
    random.shuffle(escolhidas)
    return escolhidas


def escolher_manchete(p, cfg):
    banco = cfg.get("manchetes", {})
    do_tema = banco.get("por_categoria", {}).get(p.get("categoria", ""), [])
    opcoes = do_tema if do_tema and random.random() < 0.5 else banco.get("gerais", [])
    return random.choice(opcoes or [["pequenos achados", "que fazem a", "diferença"]])


# ------------------------------------------------------------- tipografia
def quebrar(texto, fnt, largura, max_linhas):
    palavras = texto.split()
    linhas, atual = [], ""
    for w in palavras:
        teste = f"{atual} {w}".strip()
        if fnt.getlength(teste) <= largura or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = w
    if atual:
        linhas.append(atual)
    if len(linhas) > max_linhas or any(fnt.getlength(l) > largura for l in linhas):
        return None
    return linhas


def texto_espacado(d, centro, texto, fnt, cor, espaco):
    larguras = [fnt.getlength(c) for c in texto]
    total = sum(larguras) + espaco * (len(texto) - 1)
    x = centro[0] - total / 2
    for c, lw in zip(texto, larguras):
        d.text((x, centro[1]), c, font=fnt, fill=cor, anchor="lm")
        x += lw + espaco


def balao(nome, cor, texto, larg_max, alt_max, giro, max_linhas=2):
    info = {**BALOES_LARGOS, **BALOES_ALTOS}[nome]
    m = kit(nome)
    proporcao = m.width / m.height
    w, h = larg_max, larg_max / proporcao
    if h > alt_max:
        h, w = alt_max, alt_max * proporcao
    img = elemento(nome, cor, round(w), round(h))
    area_w, area_h = w * info.get("w", 0.84), h * info.get("h", 0.72)
    for tam in range(54, 23, -2):
        f = fonte("Caveat-Bold", tam)
        linhas = quebrar(texto, f, area_w, max_linhas)
        alt_linha = tam * 0.95
        if linhas and alt_linha * len(linhas) <= area_h:
            break
    d = ImageDraw.Draw(img)
    cx = w / 2 + info.get("dx", 0) * w
    cy = h / 2 + info.get("dy", 0) * h - alt_linha * (len(linhas) - 1) / 2
    for i, linha in enumerate(linhas):
        d.text((cx, cy + i * alt_linha), linha, font=f, fill=TEXTO, anchor="mm")
    return img.rotate(giro, expand=True, resample=Image.BICUBIC)


def seta_apontando(cor, origem, alvo, comprimento=180):
    """Seta do kit girada para apontar de 'origem' para 'alvo'. Retorna (imagem, x, y)."""
    nome = random.choice(list(SETAS))
    angulo_desejado = math.degrees(math.atan2(alvo[1] - origem[1], alvo[0] - origem[0]))
    img = elemento(nome, cor, comprimento)
    angulo_seta = SETAS[nome]
    espelhada = 180 - angulo_seta
    giro = angulo_seta - angulo_desejado
    giro_esp = espelhada - angulo_desejado
    giro = (giro + 180) % 360 - 180
    giro_esp = (giro_esp + 180) % 360 - 180
    if abs(giro_esp) < abs(giro):
        img, giro = ImageOps.mirror(img), giro_esp
    img = img.rotate(giro, expand=True, resample=Image.BICUBIC)
    return img, origem[0] - img.width / 2, origem[1] - img.height / 2


# ------------------------------------------------------------- post anotado
def post_anotado(foto, p, cfg, destino, frases=None, manchete=None):
    """Gera a arte 1080x1350 do post anotado e salva em 'destino' (JPEG)."""
    frases = frases or escolher_frases(p, cfg)
    abertura, meio, destaque = manchete or escolher_manchete(p, cfg)
    fundo, cores = escolher_cores(foto)
    espelho = random.random() < 0.5

    def X(x, largura=0):  # espelha a posição horizontal
        return W - x - largura if espelho else x

    img = Image.new("RGBA", (W, H), fundo + (255,))
    d = ImageDraw.Draw(img)

    # Arroba no topo
    arroba = cfg.get("arroba_instagram", "@radardosachadosof").upper()
    texto_espacado(d, (W / 2, 50), arroba, fonte("Poppins-SemiBold", 20), TEXTO_2, 4)

    # Manchete: abertura manuscrita + parte em negrito + palavra em destaque sobre pincelada
    d.text((W / 2, 100), abertura, font=fonte("Caveat-Medium", 54), fill=TEXTO, anchor="mm")
    tam = 76
    while True:
        f_b = fonte("Poppins-ExtraBold", tam)
        w_meio = f_b.getlength(meio)
        w_dest = f_b.getlength(destaque) + 56
        if w_meio + 14 + w_dest <= 960 or tam <= 56:
            break
        tam -= 4
    uma_linha = w_meio + 14 + w_dest <= 960
    pincel = random.choice(DESTAQUES)
    cor_dest = cores[0]
    alt_pincel = round(tam * 1.6)
    marca = elemento(pincel, CORES[cor_dest], round(w_dest), alt_pincel)
    ImageDraw.Draw(marca).text((marca.width / 2, marca.height / 2), destaque, font=f_b, fill=TEXTO, anchor="mm")
    marca = marca.rotate(random.choice([-2, -1.5, 1.5, 2]), expand=True, resample=Image.BICUBIC)
    if uma_linha:
        y = 205
        x0 = (W - (w_meio + 14 + w_dest)) / 2
        d.text((x0, y), meio, font=f_b, fill=TEXTO, anchor="lm")
        colar(img, marca, x0 + w_meio + 14 + (w_dest - marca.width) / 2, y - marca.height / 2)
        topo_cartao = 300
    else:
        d.text((W / 2, 195), meio, font=f_b, fill=TEXTO, anchor="mm")
        colar(img, marca, (W - marca.width) / 2, 280 - marca.height / 2)
        topo_cartao = 360

    # Cartão branco com a foto do produto (ocupa a maior parte da arte)
    cx0, cx1, base_cartao = 70, W - 70, 1150
    cw, ch = cx1 - cx0, base_cartao - topo_cartao
    sombra = tuple(int(c * 0.94) for c in fundo)
    d.ellipse([cx0 + 20, base_cartao - 20, cx1 - 20, base_cartao + 26], fill=sombra)
    cartao = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
    if fundo_branco(foto):
        prod = cortar_margem_branca(foto.convert("RGB"))
        prod = ImageOps.contain(prod, (cw - 120, ch - 110), Image.LANCZOS)
        cartao.paste(prod, ((cw - prod.width) // 2, (ch - prod.height) // 2))
    else:
        prod = ImageOps.fit(foto.convert("RGB"), (cw, ch), Image.LANCZOS)
        cartao.paste(prod, (0, 0))
    mascara = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mascara).rounded_rectangle([0, 0, cw - 1, ch - 1], radius=48, fill=255)
    cartao.putalpha(mascara)
    colar(img, cartao, cx0, topo_cartao)
    centro_produto = (W / 2, topo_cartao + ch / 2)

    # Rabiscos (atrás dos balões)
    grandes = random.sample(RABISCOS_GRANDES, 1)
    pequenos = random.sample(RABISCOS_PEQUENOS, 3)
    lugares = [
        (pequenos[0], 60, X(80, 60), 72),
        (pequenos[1], 40, X(W - 110, 40), 70),
        (pequenos[2], 84, X(W - 190, 84), base_cartao - 130),
    ]
    if fundo_branco(foto):
        lugares.append((grandes[0], 160, X(W - 250, 160), topo_cartao + 25))
    for nome, larg, x, y in lugares:
        cor = CORES[random.choice(cores)]
        r = elemento(nome, cor, larg)
        r = r.rotate(random.uniform(-15, 15), expand=True, resample=Image.BICUBIC)
        colar(img, r, x, y)

    # Balões: 2 largos (topo e base) + 1 alto (meio, do outro lado)
    cores_baloes = (cores * 2)[1:4] if len(cores) > 1 else cores * 3
    largos = random.sample(list(BALOES_LARGOS), 2)
    alto = random.choice(list(BALOES_ALTOS))

    b1 = balao(largos[0], CORES[cores_baloes[0]], frases[0], 470, 125, -4 if not espelho else 4)
    x1, y1 = X(30, b1.width), topo_cartao + 50
    colar(img, b1, x1, y1)

    b2 = balao(alto, CORES[cores_baloes[1]], frases[1] if len(frases) > 1 else "achado do dia", 320, 230,
               5 if not espelho else -5, max_linhas=3)
    x2, y2 = X(W - 25 - b2.width, b2.width), topo_cartao + 300
    colar(img, b2, x2, y2)

    if len(frases) > 2:
        b3 = balao(largos[1], CORES[cores_baloes[2]], frases[2], 540, 190, -3 if not espelho else 3)
        x3, y3 = X(30, b3.width), base_cartao + 45 - b3.height
        colar(img, b3, x3, y3)

    # Setas saindo dos balões em direção ao produto
    for (bx, by, bw, bh) in [(x1, y1, b1.width, b1.height), (x2, y2, b2.width, b2.height)]:
        cxb, cyb = bx + bw / 2, by + bh / 2
        dx, dy = centro_produto[0] - cxb, centro_produto[1] - cyb
        dist = math.hypot(dx, dy) or 1
        origem = (cxb + dx / dist * (bw / 2 + 45), cyb + dy / dist * (bh / 2 + 35))
        s, sx, sy = seta_apontando(CORES[random.choice(cores)] if random.random() < 0.6 else TEXTO, origem, centro_produto, comprimento=135)
        colar(img, s, sx, sy)

    # Rodapé
    f_cta = fonte("Poppins-Regular", 30)
    f_cta_b = fonte("Poppins-ExtraBold", 30)
    partes = ["comente ", "ACHADO", " que eu te mando o link"]
    larguras = [f_cta.getlength(partes[0]), f_cta_b.getlength(partes[1]) + 28, f_cta.getlength(partes[2])]
    x = (W - sum(larguras)) / 2
    y = H - 98
    pilula = CORES["amarelo"] if "amarelo" in cores else CORES[cores[-1]]
    d.text((x, y), partes[0], font=f_cta, fill=TEXTO, anchor="lm")
    x += larguras[0]
    d.rounded_rectangle([x, y - 24, x + larguras[1], y + 24], radius=10, fill=pilula)
    d.text((x + larguras[1] / 2, y + 1), partes[1], font=f_cta_b, fill=TEXTO, anchor="mm")
    x += larguras[1]
    d.text((x, y), partes[2], font=f_cta, fill=TEXTO, anchor="lm")
    rodape = f"#PUBLI · ACHADO Nº {p.get('numero', '')} · LINK NA BIO"
    texto_espacado(d, (W / 2, H - 52), rodape, fonte("Poppins-SemiBold", 18), TEXTO_2, 3)

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(destino, "JPEG", quality=92)
    return {"frases": frases, "manchete": [abertura, meio, destaque], "cores": cores}
