# 📡 Radar dos Achados: automação gratuita

Busca ofertas na Shopee, cria a arte, publica no Instagram (e depois no Pinterest) e mantém uma **vitrine** com todos os links, que vai na bio do Instagram. Tudo roda de graça no GitHub, mesmo com o seu computador desligado.

## Como funciona

```
A cada 3h (9h, 12h, 15h, 18h, 21h)
  │
  ├─ 1. Busca produtos na API de afiliados da Shopee (3 palavras-chave sorteadas)
  ├─ 2. Filtra: desconto ≥ 20%, nota ≥ 4,6, +100 vendas, preço entre R$10 e R$300,
  │      e não repete o mesmo produto por 30 dias
  ├─ 3. Escolhe a melhor oferta, detecta se o preço baixou e gera a legenda
  ├─ 4. Cria a arte 1080x1350 com preço, desconto e número do achado
  ├─ 5. Atualiza a vitrine (site com todos os links de afiliado)
  └─ 6. Publica no Instagram (e no Pinterest, quando liberado)
```

**Por que tem vitrine?** Link em legenda do Instagram não é clicável. O post diz "procure o achado nº 123", e a pessoa digita o número na vitrine, que fica na sua bio.

**Custo: R$ 0.** O GitHub Actions é gratuito para repositório público, o GitHub Pages hospeda a vitrine e as APIs da Shopee, do Instagram e do Pinterest são gratuitas.

---

## Passo 1: Criar o repositório no GitHub

1. Crie uma conta em [github.com](https://github.com), se ainda não tiver.
2. Clique em **New repository**, dê o nome `radar-dos-achados` e marque **Public**. Precisa ser público para as imagens e a vitrine ficarem acessíveis, mas as senhas continuam protegidas nos *Secrets*.
3. Clique em **uploading an existing file** e arraste **todo o conteúdo** desta pasta.
   - ⚠️ Confira depois se a pasta `.github/workflows` subiu. Às vezes o Windows esconde pastas que começam com ponto. Se ela não aparecer, use **Add file → Create new file**, digite o nome `.github/workflows/postar.yml` e cole o conteúdo do arquivo. Faça o mesmo com o `renovar-token.yml`.
4. Abra o `config.json` (clique no arquivo e depois no lápis ✏️) e confira o `arroba_instagram`.

## Passo 2: Ligar a vitrine (GitHub Pages)

1. No repositório, vá em **Settings → Pages**.
2. Em *Source*, escolha **Deploy from a branch**, depois a branch **main** e a pasta **/docs**, e clique em **Save**.
3. Em 1 ou 2 minutos, aparece o endereço `https://SEU-USUARIO.github.io/radar-dos-achados/`.
4. **Coloque esse link na bio do Instagram.**

## Passo 3: Cadastrar as senhas (Secrets)

Vá em **Settings → Secrets and variables → Actions → New repository secret** e cadastre:

| Nome | Onde pegar |
|---|---|
| `SHOPEE_APP_ID` | Portal de afiliados da Shopee → Open API |
| `SHOPEE_SECRET` | Mesmo lugar (a chave secreta) |
| `IG_TOKEN` | Passo 4 abaixo |
| `GH_PAT` | Passo 5 abaixo |

## Passo 4: Token do Instagram

Esse é o passo mais chato, mas só é feito uma vez.

1. Acesse [developers.facebook.com](https://developers.facebook.com) com o seu Facebook e clique em **Meus apps → Criar app**.
2. No caso de uso, escolha a opção de **gerenciar mensagens e conteúdo no Instagram** (o nome exato pode mudar um pouco).
3. No painel do app, vá em **Instagram → Configuração da API com login do Instagram**.
4. Em **Funções do app → Funções**, adicione o @ do Radar dos Achados como **Testador do Instagram**. Depois aceite o convite pelo Instagram (Configurações → Apps e sites → Convites de testador). Alguns painéis pulam essa etapa.
5. Volte em *Configuração da API*, clique em **Adicionar conta** e depois em **Gerar token** para a conta do Radar. Faça login e autorize.
6. Copie o token (é longo e vale 60 dias) e cadastre como `IG_TOKEN`.

> O app pode continuar em "modo de desenvolvimento". Para publicar **na sua própria conta**, não precisa de revisão da Meta.

## Passo 5: Renovação automática do token

O token do Instagram vence em 60 dias. O fluxo `Renovar token do Instagram` renova toda segunda-feira, mas precisa de uma chave do GitHub para atualizar o secret:

1. Acesse **github.com → sua foto → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Em *Repository access*, escolha **Only select repositories** e depois `radar-dos-achados`.
3. Em *Permissions → Repository permissions*, deixe **Secrets** como **Read and write**.
4. Em *Expiration*, escolha o prazo máximo que aparecer. Anote a data para gerar outro quando vencer.
5. Gere o token e cadastre como secret `GH_PAT`.

## Passo 6: Testar

1. Vá em **Settings → Secrets and variables → Actions → aba Variables → New repository variable**, com nome `DRY_RUN` e valor `1`. Com isso, o sistema faz tudo **menos** postar.
2. Vá em **Actions → Postar achado → Run workflow**.
3. Abra a execução e veja o log. Deve aparecer a legenda e "[SIMULAÇÃO] Publicaria...". Confira também a arte em `docs/img/` e a vitrine.
4. Se estiver tudo certo, mude a variável `DRY_RUN` para `0` e rode de novo. **O primeiro post real vai sair.** 🎉

A partir daí, os posts saem sozinhos nos horários programados.

---

## Passo 7 (depois): Pinterest

O Pinterest libera a API em duas fases. Na fase de **teste (Trial)**, os pins só aparecem para você, no *sandbox*. Para eles ficarem **públicos**, é preciso pedir o **acesso Standard**, que exige enviar um vídeo curto mostrando o app funcionando.

1. Converta o Pinterest do Radar em **conta comercial** (é grátis, pelas configurações do app).
2. Em [developers.pinterest.com](https://developers.pinterest.com), clique em **My apps → Connect app**. Preencha:
   - Site: o link da sua vitrine
   - Política de privacidade: `https://SEU-USUARIO.github.io/radar-dos-achados/privacidade.html` (a página já foi criada para você)
   - Redirect URI: `https://localhost/`
3. Depois da aprovação do Trial, rode no seu computador (precisa ter o Python instalado):
   ```
   pip install requests
   python ferramentas/pinterest_token.py
   ```
   O script mostra o `PINTEREST_REFRESH_TOKEN` e os IDs das suas pastas. Cadastre os secrets `PINTEREST_REFRESH_TOKEN`, `PINTEREST_APP_ID` e `PINTEREST_APP_SECRET`.
4. No `config.json`, em `"pinterest"`, coloque `"ativo": true` e `"sandbox": true` e cole o `board_id` da pasta de teste.
5. Rode o workflow e **grave a tela** mostrando o pin sendo criado. Esse é o vídeo para pedir o **Standard access** no painel do app.
6. Quando o Standard for aprovado, mude para `"sandbox": false` e troque o `board_id` pelo da pasta real.

Os pins apontam para uma página da sua vitrine (`/o/123.html`) com o botão "Ver oferta na Shopee". Isso evita que o Pinterest bloqueie links encurtados.

---

## Personalizar

Tudo que você pode ajustar está no `config.json`:

- **`palavras_chave`**: o que o radar procura. **Dica:** foque num nicho (casa, beleza, maternidade, pet...). Perfil de nicho cresce e vende mais do que perfil "de tudo".
- **`filtros`**: desconto mínimo, nota, vendas, faixa de preço e palavras proibidas.
- **Horários**: ficam no arquivo `.github/workflows/postar.yml`, na linha `cron`. O horário é UTC (3 horas a mais que Brasília).
- **Legenda**: as frases de abertura estão em `ABERTURAS`, dentro do `radar.py`.

## Cuidados

- **Poste com moderação.** 4 ou 5 posts por dia é um bom limite. Postar demais derruba o alcance e parece spam.
- **Sempre identifique como publicidade.** A legenda já inclui `#publi • link de afiliado`, que é o que o CONAR pede.
- **O horário pode atrasar.** O agendamento do GitHub às vezes atrasa de 5 a 30 minutos. É normal.
- **Stories e Reels geram mais vendas** que post no feed, e a próxima evolução pode ser publicar Stories com link.

## Problemas comuns

| Mensagem no log | O que fazer |
|---|---|
| `Invalid Signature` / erro 401 da Shopee | Confira `SHOPEE_APP_ID` e `SHOPEE_SECRET` (sem espaços antes ou depois) |
| `Nenhuma oferta passou nos filtros` | Afrouxe os filtros ou troque as palavras-chave |
| `Instagram (container)` com `OAuthException` | O token venceu ou está errado: gere de novo (Passo 4) |
| `Instagram recusou a imagem` | Rode de novo. Às vezes a imagem ainda não estava pública; o sistema tenta até 3 vezes |
| O workflow não aparece em *Actions* | A pasta `.github/workflows` não subiu (veja o Passo 1) |
