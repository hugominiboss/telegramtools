=== COMO RODAR O SCRAPER NO WINDOWS (SEM UBUNTU, SEM POWERHELL) ===

1. Instale o Python 3.10 ou 3.11:
   - Baixe em: https://www.python.org/ftp/python/3.11.6/python-3.11.6-amd64.exe
   - Na tela de instalação, MARQUE a opção "Add Python to PATH"

2. Abra o Prompt de Comando (CMD):
   - Pressione Win + R → digite cmd → Enter

3. Vá até a pasta do projeto:
   cd %USERPROFILE%\Desktop\scraper-grupos

4. Instale as dependências:
   pip install -r requirements.txt

5. Pegue sua API do Telegram:
   - Acesse https://my.telegram.org/auth
   - Crie uma aplicação e anote:
     * api_id (número)
     * api_hash (string longa)
     * seu telefone com DDD (ex: 5511999999999)

6. Rode o script (troque pelos seus dados):
   set TG_API_ID=123456
   set TG_HASH=abcd1234abcd1234abcd1234abcd1234
   set TG_PHONE=5511999999999
   python scraper.py

7. Pronto! Os links vivos aparecerão em:
   grupos_vivos.txt
