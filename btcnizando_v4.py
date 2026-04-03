import feedparser
import requests
import re
import os
import time
import base64
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

WP_URL          = os.getenv("WP_URL")
WP_USERNAME     = os.getenv("WP_USERNAME")
WP_APP_PASS     = os.getenv("WP_APP_PASSWORD")
GEMINI_KEY      = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID")

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

TELEGRAM_API = "https://api.telegram.org/bot" + TELEGRAM_TOKEN

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://cryptopotato.com/feed/",
    "https://cryptonews.com/news/feed/",
    "https://guiadobitcoin.com.br/feed/",
    "https://br.investing.com/rss/news_40.rss",
    "https://www.infomoney.com.br/feed/"
]

KEYWORDS_SCORE = {
    10: ["bitcoin","btc","ethereum","eth"],
    8:  ["sec","cvm","regulacao","regulation","fed","banco central"],
    7:  ["etf","blackrock","fidelity","coinbase","binance"],
    6:  ["hack","exploit","breach","arrest","seized"],
    5:  ["defi","layer2","fork","upgrade","protocol"],
    3:  ["altcoin","token","nft","meme"]
}

# ─── TELEGRAM ────────────────────────────────────────────

def tg_send(msg):
    try:
        requests.post(TELEGRAM_API + "/sendMessage", data={
            "chat_id": TELEGRAM_CHAT,
            "text": msg,
            "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        print("TG erro:", e)

def tg_send_image(img_bytes, caption):
    try:
        requests.post(TELEGRAM_API + "/sendPhoto", data={
            "chat_id": TELEGRAM_CHAT,
            "caption": caption,
            "parse_mode": "Markdown"
        }, files={"photo": img_bytes}, timeout=30)
    except Exception as e:
        print("TG imagem erro:", e)

def tg_get_updates(offset=None):
    params = {"timeout": 30, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(TELEGRAM_API + "/getUpdates", params=params, timeout=35)
        return r.json().get("result", [])
    except:
        return []

def tg_aguardar_resposta(update_id):
    """Aguarda mensagem do usuário e retorna o texto"""
    offset = update_id + 1
    while True:
        updates = tg_get_updates(offset)
        for upd in updates:
            msg = upd.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id == TELEGRAM_CHAT:
                return upd["update_id"], msg.get("text", "").strip()
        time.sleep(2)

# ─── RSS + SCORE ─────────────────────────────────────────

def parse_data_entry(entry):
    for campo in ["published_parsed", "updated_parsed"]:
        val = getattr(entry, campo, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except:
                continue
    return None

def calcular_score(noticia):
    texto = (noticia["title"] + " " + noticia["summary"]).lower()
    score = 0
    for pts, kws in KEYWORDS_SCORE.items():
        for kw in kws:
            if kw in texto:
                score += pts
    score += min(len(noticia["summary"]) // 100, 5)
    return score

def coletar_noticias(max_artigos=5):
    tg_send("📡 Coletando RSS — filtrando últimas 24h...")
    agora  = datetime.now(timezone.utc)
    limite = agora - timedelta(hours=24)
    todas  = []

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            nome = feed.feed.get("title", url)
            for entry in feed.entries[:10]:
                data = parse_data_entry(entry)
                if data and data < limite:
                    continue
                noticia = {
                    "title":    entry.title,
                    "link":     entry.link,
                    "summary":  getattr(entry, "summary", "")[:600],
                    "source":   nome,
                    "data":     data,
                    "data_str": data.strftime("%d/%m/%Y %H:%M UTC") if data else "data desconhecida"
                }
                noticia["score"] = calcular_score(noticia)
                todas.append(noticia)
        except Exception as e:
            print("Feed erro:", url, e)
            continue

    todas.sort(key=lambda x: x["score"], reverse=True)

    unicas = []
    titulos_vistos = []
    for n in todas:
        titulo_norm = re.sub(r"[^a-z0-9]", "", n["title"].lower())[:40]
        if titulo_norm not in titulos_vistos:
            titulos_vistos.append(titulo_norm)
            unicas.append(n)

    return unicas[:max_artigos]

# ─── CLASSIFICAR ─────────────────────────────────────────

def classificar(noticia):
    texto = (noticia["title"] + " " + noticia["summary"]).lower()
    tipos = {
        "breaking":      ["hack","suspendeu","breach","exploit","seized","arrest","crash","surge"],
        "institucional": ["bank","banco","fundo","fund","etf","treasury","buys","comprou","adquiriu"],
        "regulacao":     ["lei","law","sec","cvm","regulation","bill","approved","ban","proibiu","aprovou"],
        "analise":       ["protocol","token","update","fork","defi","layer2","upgrade","lancou"]
    }
    for tipo, kws in tipos.items():
        if any(k in texto for k in kws):
            return tipo
    return "analise"

# ─── TRADUZIR TÍTULO ─────────────────────────────────────

def traduzir_titulo(titulo):
    try:
        resp = model.generate_content(
            "Traduza este titulo de noticia para portugues BR jornalistico. "
            "Retorne APENAS o titulo traduzido, sem explicacoes, sem aspas, sem ponto final:\n" + titulo
        )
        return resp.text.strip()
    except:
        return titulo

# ─── ESCREVER ARTIGO ─────────────────────────────────────

def escrever_artigo(noticia, tipo):
    titulo_pt = traduzir_titulo(noticia["title"])
    noticia["title"] = titulo_pt
    tg_send("✍️ Gerando artigo: *" + titulo_pt + "*")

    estruturas = {
        "breaking":      "H2s: O que se sabe ate agora | Reacao da comunidade | Contexto rapido",
        "institucional": "H2s: O que foi anunciado | Como funciona na pratica | Por que a empresa decidiu | Precedente no setor | Proximos passos",
        "regulacao":     "H2s: O que foi aprovado | O que muda na pratica | O que diz o texto | Reacao do setor | Proximos passos",
        "analise":       "H2s: O que e o projeto | Como funciona tecnicamente | O que mudou | Quem esta por tras | Casos de uso | Pontos de atencao"
    }
    prompt = (
        "Voce e redator senior do BTCnizando.com.br, portal jornalistico de criptomoedas.\n"
        "Estilo: Portal do Bitcoin + CoinTelegraph Brasil.\n"
        "Todo o conteudo DEVE estar em portugues BR.\n\n"
        "DATA DA NOTICIA: " + noticia["data_str"] + "\n"
        "TIPO: " + tipo.upper() + "\n"
        "TITULO (ja em portugues, use exatamente este): " + titulo_pt + "\n"
        "FONTE: " + noticia["source"] + " - " + noticia["link"] + "\n"
        "RESUMO: " + noticia["summary"] + "\n\n"
        "ESTRUTURA: " + estruturas[tipo] + "\n\n"
        "REGRAS ABSOLUTAS:\n"
        "- Retorne APENAS o conteudo HTML do corpo do artigo\n"
        "- NUNCA inclua ```html ou ``` no retorno\n"
        "- NUNCA inclua titulo H1 no conteudo\n"
        "- Comece direto com <p> ou primeiro <h2>\n"
        "- Use apenas: <h2> <h3> <p> <a href> <ul> <li> <strong>\n"
        "- Portugues BR fluente e jornalistico\n"
        "- ZERO recomendacao financeira\n"
        "- Minimo 2 links externos incluindo " + noticia["link"] + "\n"
        "- 1 link interno para btcnizando.com.br\n"
        "- Minimo 500 palavras\n\n"
        "Apos o HTML entregue fora do HTML:\n"
        "---YOAST---\n"
        "Focus Keyword: [1-4 palavras em portugues]\n"
        "SEO Title: [max 60 chars em portugues]\n"
        "Meta Description: [120-155 chars em portugues]\n"
        "Slug: [kebab-case-max-5-palavras em portugues]\n"
        "---FIM---"
    )
    try:
        resp  = model.generate_content(prompt)
        texto = resp.text
    except Exception as e:
        tg_send("❌ Gemini erro: " + str(e))
        texto = "<p>Erro ao gerar artigo.</p>\n---YOAST---\nFocus Keyword: Bitcoin\nSEO Title: " + titulo_pt[:60] + "\nMeta Description: " + noticia["summary"][:150] + "\nSlug: artigo-bitcoin\n---FIM---"

    html_content = texto
    yoast_raw    = ""
    if "---YOAST---" in texto:
        partes       = texto.split("---YOAST---")
        html_content = partes[0].strip()
        yoast_raw    = partes[1].split("---FIM---")[0] if "---FIM---" in partes[1] else partes[1]

    html_content = re.sub(r"```html?\s*", "", html_content)
    html_content = re.sub(r"```\s*",      "", html_content)
    html_content = re.sub(r"<h1>.*?</h1>", "", html_content, flags=re.IGNORECASE|re.DOTALL)
    html_content = html_content.strip()

    yoast = {}
    for campo in ["Focus Keyword", "SEO Title", "Meta Description", "Slug"]:
        m = re.search(campo + r":\s*(.+)", yoast_raw)
        yoast[campo] = m.group(1).strip() if m else ""

    slug = yoast.get("Slug") or re.sub(r"[^a-z0-9]+", "-", titulo_pt.lower())[:50].strip("-")
    yoast["Slug"] = slug

    return {
        "title":   titulo_pt,
        "content": html_content,
        "excerpt": noticia["summary"][:150],
        "slug":    slug,
        "yoast":   yoast,
        "tipo":    tipo,
        "data":    noticia.get("data_str", "")
    }

# ─── IMAGEM ──────────────────────────────────────────────

def gerar_prompt_imagem(artigo):
    prompt = (
        "Crie um prompt em ingles para imagem editorial profissional.\n"
        "Titulo do artigo: " + artigo["title"] + "\n"
        "Tipo: " + artigo["tipo"] + "\n\n"
        "REGRAS:\n"
        "- Estilo fotojornalismo editorial financeiro\n"
        "- Sem texto na imagem\n"
        "- Sem rostos reconheciveis\n"
        "- Cores: laranja Bitcoin (#F7931A) + preto + branco\n"
        "- 2-3 linhas em ingles\n"
        "Entregue APENAS o prompt."
    )
    try:
        resp = model.generate_content(prompt)
        return resp.text.strip()
    except:
        return "Professional editorial photo, Bitcoin orange glowing coin on dark background, financial technology, no text, no faces"

def gerar_imagem_pollinations(prompt_img):
    tg_send("🎨 Gerando imagem...")
    try:
        prompt_enc = requests.utils.quote(prompt_img)
        url = "https://image.pollinations.ai/prompt/" + prompt_enc + "?width=1280&height=720&nologo=true"
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
            tg_send("✅ Imagem gerada!")
            return r.content
        else:
            tg_send("❌ Pollinations erro: " + str(r.status_code))
            return None
    except Exception as e:
        tg_send("❌ Imagem excecao: " + str(e))
        return None

# ─── VERIFICAR ───────────────────────────────────────────

def verificar(artigo):
    texto = artigo["content"]
    yoast = artigo["yoast"]
    checks = {
        "tamanho":   len(texto.split()) >= 400,
        "sem_h1":    "<h1>" not in texto.lower(),
        "sem_bloco": "```" not in texto,
        "seo_title": 1 <= len(yoast.get("SEO Title", "")) <= 65,
        "meta":      50 <= len(yoast.get("Meta Description", "")) <= 160,
        "slug":      bool(re.match(r"^[a-z0-9][a-z0-9\-]{2,58}$", yoast.get("Slug", ""))),
        "keyword":   bool(yoast.get("Focus Keyword")),
        "links":     texto.count("http") >= 2
    }
    score  = sum(checks.values())
    total  = len(checks)
    return score >= total * 0.6

# ─── PUBLICAR WP ─────────────────────────────────────────

def publicar_wp(artigo):
    payload = {
        "title":   artigo["title"],
        "content": artigo["content"],
        "status":  "draft",
        "slug":    artigo["yoast"].get("Slug", ""),
        "excerpt": artigo["excerpt"],
        "meta": {
            "_yoast_wpseo_focuskw":  artigo["yoast"].get("Focus Keyword", ""),
            "_yoast_wpseo_title":    artigo["yoast"].get("SEO Title", ""),
            "_yoast_wpseo_metadesc": artigo["yoast"].get("Meta Description", "")
        }
    }
    r = requests.post(
        WP_URL + "/wp-json/wp/v2/posts",
        auth=(WP_USERNAME, WP_APP_PASS),
        json=payload,
        timeout=30
    )
    if r.status_code == 201:
        pid  = r.json()["id"]
        edit = WP_URL + "/wp-admin/post.php?post=" + str(pid) + "&action=edit"
        return pid, edit
    tg_send("❌ WP erro " + str(r.status_code) + ": " + r.text[:200])
    return None, None

# ─── PIPELINE PRINCIPAL ──────────────────────────────────

def pipeline_aprovar_e_publicar(noticias):
    aprovadas = []
    last_update_id = 0

    # Busca o ultimo update_id para ignorar mensagens antigas
    updates = tg_get_updates()
    if updates:
        last_update_id = updates[-1]["update_id"]

    for i, noticia in enumerate(noticias, 1):
        msg = (
            "📰 *Notícia " + str(i) + "/" + str(len(noticias)) + "*\n\n"
            "*" + noticia["title"] + "*\n\n"
            "📅 " + noticia["data_str"] + "\n"
            "🏆 Score: " + str(noticia["score"]) + "\n"
            "🔗 " + noticia["link"] + "\n\n"
            "📝 " + noticia["summary"][:200] + "...\n\n"
            "Publicar este artigo? ✅ sim  ❌ não"
        )
        tg_send(msg)

        last_update_id, resposta = tg_aguardar_resposta(last_update_id)
        resposta = resposta.lower().strip()

        if resposta in ["✅", "sim", "s", "ok", "yes", "y"]:
            aprovadas.append(noticia)
            tg_send("✅ Aprovado! Próximo...")
        else:
            tg_send("❌ Pulado.")

        time.sleep(1)

    if not aprovadas:
        tg_send("⚠️ Nenhuma notícia aprovada. Encerrando.")
        return

    tg_send("\n🚀 *" + str(len(aprovadas)) + " notícias aprovadas! Iniciando geração...*")

    for i, noticia in enumerate(aprovadas, 1):
        tg_send("\n📝 Processando " + str(i) + "/" + str(len(aprovadas)) + "...")
        tipo   = classificar(noticia)
        artigo = escrever_artigo(noticia, tipo)

        if not verificar(artigo):
            tg_send("⚠️ Artigo reprovado no checklist. Pulando.")
            continue

        pid, edit = publicar_wp(artigo)
        if not pid:
            continue

        prompt_img = gerar_prompt_imagem(artigo)
        img_bytes  = gerar_imagem_pollinations(prompt_img)

        if img_bytes:
            caption = (
                "🖼️ *Imagem de capa*\n"
                "*" + artigo["title"] + "*\n\n"
                "✏️ [Editar no WP](" + edit + ")\n\n"
                "📋 *SEO Yoast:*\n"
                "Keyword: `" + artigo["yoast"].get("Focus Keyword", "") + "`\n"
                "Title: `" + artigo["yoast"].get("SEO Title", "") + "`\n"
                "Meta: `" + artigo["yoast"].get("Meta Description", "") + "`\n"
                "Slug: `" + artigo["yoast"].get("Slug", "") + "`"
            )
            tg_send_image(img_bytes, caption)
        else:
            tg_send(
                "⚠️ Imagem não gerada. Adicione manualmente.\n\n"
                "🎉 *Rascunho criado!*\n"
                "✏️ " + edit + "\n\n"
                "📋 *SEO Yoast:*\n"
                "Keyword: `" + artigo["yoast"].get("Focus Keyword", "") + "`\n"
                "Title: `" + artigo["yoast"].get("SEO Title", "") + "`\n"
                "Meta: `" + artigo["yoast"].get("Meta Description", "") + "`\n"
                "Slug: `" + artigo["yoast"].get("Slug", "") + "`"
            )

        time.sleep(5)

    tg_send("🏁 *Pipeline finalizado!* " + datetime.now().strftime("%d/%m/%Y %H:%M"))

# ─── LOOP PRINCIPAL ──────────────────────────────────────

def main():
    tg_send("🤖 *BTCnizando Bot ativo!*\nMande /rodar para iniciar o pipeline.")
    print("Bot ativo — aguardando /rodar no Telegram...")

    last_update_id = 0
    updates = tg_get_updates()
    if updates:
        last_update_id = updates[-1]["update_id"]

    while True:
        updates = tg_get_updates(last_update_id + 1)
        for upd in updates:
            last_update_id = upd["update_id"]
            msg  = upd.get("message", {})
            chat = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()

            if chat != TELEGRAM_CHAT:
                continue

            if text == "/rodar":
                tg_send("🚀 Iniciando coleta de notícias...")
                noticias = coletar_noticias(max_artigos=5)
                if not noticias:
                    tg_send("⚠️ Nenhuma notícia encontrada nas últimas 24h!")
                    continue
                tg_send("✅ *" + str(len(noticias)) + " notícias encontradas!*\nVou enviar uma por uma para aprovação...")
                time.sleep(1)
                pipeline_aprovar_e_publicar(noticias)

            elif text == "/status":
                tg_send("✅ Bot ativo — " + datetime.now().strftime("%d/%m/%Y %H:%M"))

            elif text == "/ajuda":
                tg_send(
                    "📋 *Comandos disponíveis:*\n\n"
                    "/rodar — coleta notícias e inicia aprovação\n"
                    "/status — verifica se o bot está ativo\n"
                    "/ajuda — mostra este menu"
                )

        time.sleep(3)

if __name__ == "__main__":
    main()
