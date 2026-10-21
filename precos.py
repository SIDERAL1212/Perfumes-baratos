#!/usr/bin/env python3
"""Rastreador de preços de perfumes.

Lê os links de produtos.json, extrai o preço de cada loja, guarda o histórico
em docs/historico.json e avisa por Telegram e/ou e-mail quando o preço cai.
"""
import json
import os
import random
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

RAIZ = Path(__file__).parent
ARQ_PRODUTOS = RAIZ / "produtos.json"
ARQ_HISTORICO = RAIZ / "docs" / "historico.json"
MAX_LEITURAS = 2000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------- utilidades
def loja_de(url):
    host = urlparse(url).netloc.lower()
    for chave, nome in (
        ("mercadolivre", "Mercado Livre"),
        ("mercadolibre", "Mercado Livre"),
        ("shopee", "Shopee"),
        ("belezanaweb", "Beleza na Web"),
        ("sephora", "Sephora"),
        ("sieno", "Sieno"),
        ("amazon", "Amazon"),
        ("amzn", "Amazon"),
    ):
        if chave in host:
            return nome
    return host


def para_float(txt):
    """Converte 'R$ 1.234,56', '1234.56', '49,9' etc. em float."""
    if txt is None:
        return None
    s = re.sub(r"[^\d,.]", "", str(txt))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "." in s:
        # '1.234' (milhar) vs '49.90' (decimal)
        if re.search(r"\.\d{3}$", s) and s.count(".") >= 1 and not re.search(r"\.\d{1,2}$", s):
            s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ----------------------------------------------------------------- extratores
def _jsonld(soup):
    achados = []

    def varrer(no):
        if isinstance(no, dict):
            tipo = no.get("@type")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if any(t in ("Offer", "AggregateOffer") for t in tipos):
                for chave in ("price", "lowPrice"):
                    if chave in no:
                        achados.append(no[chave])
            for v in no.values():
                varrer(v)
        elif isinstance(no, list):
            for v in no:
                varrer(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            varrer(json.loads(tag.string or tag.get_text() or ""))
        except (ValueError, TypeError):
            continue
    for a in achados:
        v = para_float(a)
        if v:
            return v
    return None


def _meta(soup):
    for attrs in (
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
    ):
        tag = soup.find(attrs=attrs)
        if tag:
            v = para_float(tag.get("content") or tag.get_text())
            if v:
                return v
    return None


def _amazon(soup):
    html = str(soup)
    if "api-services-support@amazon.com" in html or "Digite os caracteres" in html:
        raise RuntimeError("Amazon pediu captcha (bloqueou o robô)")
    for seletor in (
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        "#apex_desktop .a-price .a-offscreen",
        ".a-price .a-offscreen",
    ):
        tag = soup.select_one(seletor)
        if tag:
            v = para_float(tag.get_text())
            if v:
                return v
    return None


def _mercadolivre(soup):
    v = _meta(soup)
    if v:
        return v
    frac = soup.select_one(
        ".ui-pdp-price__second-line .andes-money-amount__fraction"
    )
    if frac:
        cents = soup.select_one(
            ".ui-pdp-price__second-line .andes-money-amount__cents"
        )
        return para_float(frac.get_text() + ("," + cents.get_text() if cents else ""))
    return None


EXTRATORES_POR_LOJA = {
    "Amazon": [_amazon],
    "Mercado Livre": [_mercadolivre],
}


def preco_do_html(html, loja):
    soup = BeautifulSoup(html, "html.parser")
    for fn in EXTRATORES_POR_LOJA.get(loja, []) + [_jsonld, _meta]:
        v = fn(soup)
        if v:
            return v
    return None


def preco_shopee(url):
    """Shopee carrega tudo por JavaScript; tenta a API interna (pode bloquear)."""
    m = re.search(r"-i\.(\d+)\.(\d+)", url) or re.search(r"/product/(\d+)/(\d+)", url)
    if not m:
        raise RuntimeError("não achei shopid/itemid no link da Shopee")
    shopid, itemid = m.groups()
    r = requests.get(
        "https://shopee.com.br/api/v4/item/get",
        params={"itemid": itemid, "shopid": shopid},
        headers={**HEADERS, "Referer": url, "X-Requested-With": "XMLHttpRequest"},
        timeout=25,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Shopee respondeu HTTP {r.status_code} (bloqueio anti-robô)")
    item = (r.json() or {}).get("data") or {}
    bruto = item.get("price") or item.get("price_min")
    if not bruto:
        raise RuntimeError("Shopee não devolveu o preço (bloqueio anti-robô)")
    return bruto / 100000


def obter_preco(url):
    loja = loja_de(url)
    if loja == "Shopee":
        return preco_shopee(url)
    ultimo_erro = None
    for tentativa in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code in (403, 429, 503):
                raise RuntimeError(f"HTTP {r.status_code} (loja bloqueou o robô)")
            r.raise_for_status()
            v = preco_do_html(r.text, loja)
            if v:
                return v
            raise RuntimeError("preço não encontrado na página")
        except Exception as e:  # noqa: BLE001
            ultimo_erro = e
            time.sleep(2 + tentativa * 3)
    raise RuntimeError(str(ultimo_erro))


# ------------------------------------------------------------------- avisos
def decidir_aviso(item, preco, anterior, alvo, queda_pct):
    motivo = None
    if alvo and preco <= alvo:
        motivo = f"abaixo do seu alvo de {brl(alvo)}"
    elif anterior and preco <= anterior * (1 - queda_pct / 100):
        pct = (1 - preco / anterior) * 100
        motivo = f"caiu {pct:.0f}% (era {brl(anterior)})"
    if motivo:
        ja = item.get("ultimo_aviso")
        if ja is None or preco < ja:
            return motivo
    return None


def enviar_telegram(texto):
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": texto, "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()
    return True


def enviar_email(assunto, texto):
    host, user = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER")
    senha, destino = os.getenv("SMTP_PASS"), os.getenv("EMAIL_DESTINO")
    if not (host and user and senha and destino):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto, user, destino
    msg.set_content(texto)
    porta = int(os.getenv("SMTP_PORT") or "465")
    if porta == 465:  # SSL direto (Gmail)
        servidor = smtplib.SMTP_SSL(host, porta, timeout=30)
    else:  # STARTTLS, porta 587 (Outlook/Hotmail)
        servidor = smtplib.SMTP(host, porta, timeout=30)
        servidor.starttls()
    with servidor as s:
        s.login(user, senha)
        s.send_message(msg)
    return True


def avisar(alertas):
    linhas = []
    for a in alertas:
        linhas.append(
            f"🔔 {a['nome']} — {a['loja']}\n   {brl(a['preco'])} ({a['motivo']})\n   {a['url']}"
        )
    texto = "Preços de perfumes caíram:\n\n" + "\n\n".join(linhas)
    enviados = []
    for nome, fn in (
        ("Telegram", lambda: enviar_telegram(texto)),
        ("E-mail", lambda: enviar_email("Perfume em promoção!", texto)),
    ):
        try:
            if fn():
                enviados.append(nome)
        except Exception as e:  # noqa: BLE001
            print(f"  ! falha ao avisar por {nome}: {e}")
    if not enviados:
        print("  ! nenhum canal de aviso configurado (veja o README)")
    return bool(enviados)


# --------------------------------------------------------------------- main
def main():
    config = json.loads(ARQ_PRODUTOS.read_text(encoding="utf-8"))
    queda_pct = float(config.get("queda_minima_pct", 5))
    historico = {"itens": {}}
    if ARQ_HISTORICO.exists():
        historico = json.loads(ARQ_HISTORICO.read_text(encoding="utf-8"))
    itens = historico.setdefault("itens", {})
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")

    alertas, total, falhas = [], 0, 0
    for perfume in config["perfumes"]:
        for url in perfume["links"]:
            if "COLE" in url:
                continue
            total += 1
            loja = loja_de(url)
            item = itens.setdefault(url, {"leituras": []})
            item.update(nome=perfume["nome"], loja=loja, alvo=perfume.get("preco_alvo"))
            try:
                preco = obter_preco(url)
            except Exception as e:  # noqa: BLE001
                falhas += 1
                item["erro"] = str(e)
                print(f"✗ {perfume['nome']} [{loja}]: {e}")
            else:
                item.pop("erro", None)
                anterior = item["leituras"][-1]["preco"] if item["leituras"] else None
                item["leituras"].append({"t": agora, "preco": round(preco, 2)})
                item["leituras"] = item["leituras"][-MAX_LEITURAS:]
                print(f"✓ {perfume['nome']} [{loja}]: {brl(preco)}")
                motivo = decidir_aviso(item, preco, anterior, perfume.get("preco_alvo"), queda_pct)
                if motivo:
                    alertas.append(dict(nome=perfume["nome"], loja=loja, preco=preco, motivo=motivo, url=url))
                    item["ultimo_aviso"] = preco
                elif item.get("ultimo_aviso") and preco > item["ultimo_aviso"]:
                    item["ultimo_aviso"] = None  # preço subiu: libera novo aviso
            time.sleep(random.uniform(2, 5))

    historico["atualizado_em"] = agora
    ARQ_HISTORICO.parent.mkdir(parents=True, exist_ok=True)
    ARQ_HISTORICO.write_text(json.dumps(historico, ensure_ascii=False, indent=1), encoding="utf-8")

    if alertas:
        avisar(alertas)
    print(f"\n{total - falhas}/{total} links lidos, {len(alertas)} aviso(s).")
    if total and falhas == total:
        sys.exit(1)


if __name__ == "__main__":
    main()
