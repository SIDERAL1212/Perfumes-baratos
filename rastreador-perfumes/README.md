# Rastreador de preços de perfumes

Acompanha o preço de perfumes em Mercado Livre, Shopee, Beleza na Web, Sephora, Sieno e Amazon.
Roda sozinho no GitHub Actions (grátis), guarda o histórico e avisa por Telegram e/ou e-mail
quando o preço cai. Um painel mostra o histórico de cada perfume lado a lado entre as lojas.

## Como colocar no ar (uns 15 minutos)

### 1. Criar o repositório
1. Crie uma conta em github.com (se ainda não tiver) e um repositório novo (pode ser privado).
2. Envie todos os arquivos desta pasta para ele (botão **Add file → Upload files**).
   Lembre de enviar também a pasta `.github` (ela guarda o agendamento).

### 2. Cadastrar os perfumes
Edite o `produtos.json`:
- `nome`: como você quer ver no painel e nos avisos.
- `preco_alvo`: avisa quando o preço ficar igual ou abaixo disso.
- `links`: link da página do produto em cada loja (pode deixar só as lojas que quiser).
- `queda_minima_pct`: também avisa se o preço cair esse percentual desde a última leitura (padrão 5%).

Para acompanhar outro perfume, copie o bloco inteiro dentro de `perfumes`.

### 3. Configurar os avisos
No repositório: **Settings → Secrets and variables → Actions → New repository secret**.

**Telegram (mais fácil):**
1. No Telegram, converse com o **@BotFather**, envie `/newbot` e siga os passos. Ele entrega um token.
2. Mande qualquer mensagem para o seu bot novo e abra
   `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` — o número em `"chat":{"id":...}` é o seu chat id.
3. Crie os secrets `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.

**E-mail (opcional):** crie `SMTP_HOST` (ex.: `smtp.gmail.com`), `SMTP_USER`, `SMTP_PASS`
(no Gmail, use uma *senha de app*) e `EMAIL_DESTINO`. `SMTP_PORT` só se não for 465.
Outlook/Hotmail: `SMTP_HOST=smtp-mail.outlook.com` e `SMTP_PORT=587` (a Microsoft vem desativando senha em envio por script; se falhar, use Telegram ou envie por um Gmail).

### 4. Ligar o painel
**Settings → Pages → Source: Deploy from a branch → Branch: main, pasta `/docs`**.
O endereço do painel aparece ali mesmo. (Em repositório privado, Pages exige plano pago do GitHub;
se preferir, deixe o repositório público — os links dos produtos ficam visíveis, mas os secrets não.)

### 5. Usar o site
O endereço do GitHub Pages abre a interface: **Painel** (preços e histórico), **Perfumes e links**
(adicionar, editar e remover perfumes) e **Conexão com o GitHub**. Para o site salvar a lista e disparar a
leitura de preços, crie um token fine-grained só para este repositório (Contents e Actions: Read and write)
e cole na aba Conexão. O token fica guardado apenas no seu navegador. Sem token, o painel funciona e você
usa "Copiar JSON" para colar em `produtos.json`.

### 6. Primeira rodada
Aba **Actions → Rastrear preços → Run workflow**. Depois disso roda sozinho a cada 4 horas
(para mudar, edite o `cron` em `.github/workflows/rastrear.yml`).

## Testar no seu computador
```
pip install -r requirements.txt
python precos.py
```
Para testar os avisos localmente, defina `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` no terminal antes.

## Quando o aviso é enviado
- Preço atual ≤ `preco_alvo`, **ou** queda ≥ `queda_minima_pct` desde a leitura anterior.
- Só avisa de novo se o preço baixar mais do que no último aviso (sem spam). Se o preço subir e depois cair, volta a avisar.

## Limitações — leia isto
- **Amazon e Shopee bloqueiam robôs**, principalmente quando vêm de servidores como o do GitHub.
  Podem funcionar às vezes e falhar em outras. Quando falham, o painel mostra o motivo
  em vermelho e o resto continua funcionando. Se for importante para você, a saída mais estável é
  rodar o script no seu próprio computador (IP residencial) em vez de no GitHub.
- **Shopee:** o link precisa ter o formato `...-i.SHOPID.ITEMID` (o padrão da página do produto).
- **Mercado Livre** às vezes mostra tela de verificação; use o link direto do anúncio.
- Lojas mudam o site de vez em quando. Se uma parar de funcionar, o ajuste é pequeno:
  a lógica de cada loja fica em `precos.py`, nas funções `_amazon`, `_mercadolivre` e `preco_shopee`
  (as demais usam dados estruturados da página, que mudam pouco).
- Este projeto foi escrito e testado com páginas de exemplo, mas **não contra os sites reais** —
  na primeira rodada, confira no log da aba Actions quais lojas foram lidas.
- Confira os termos de uso das lojas; use com moderação (o script já espera alguns segundos entre pedidos).
