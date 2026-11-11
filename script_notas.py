import os
import json
import time
import logging
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

# ----------------------------------------------------------------------
# 1. CONFIGURAÇÕES E LOGS
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("lancamento_notas.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

load_dotenv()

# ----------------------------------------------------------------------
# 2. ESTRUTURA DO SCHEMA DE DADOS (3 NOTAS POR ALUNO)
# ----------------------------------------------------------------------
class NotasTriplasMapeadas(BaseModel):
    id_aluno: str
    nome_oficial: str
    nota1: Optional[float] = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    nota2: Optional[float] = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    nota3: Optional[float] = Field(default=None, ge=0, le=10, allow_inf_nan=False)

class ListaNotasMapeadas(BaseModel):
    turma: str
    alunos: List[NotasTriplasMapeadas]

# ----------------------------------------------------------------------
# 3. VALIDAÇÃO DE NOTAS
# ----------------------------------------------------------------------
def validar_e_formatar_nota(valor: Optional[float]) -> Optional[str]:
    if valor is None:
        return None
    try:
        nota_float = float(valor)
        if not (0.0 <= nota_float <= 10.0):
            logging.error(f"⚠️ Nota fora do intervalo permitido (0.0 - 10.0): {valor}")
            return None
        return f"{nota_float:.1f}".replace('.', ',')
    except (ValueError, TypeError):
        logging.error(f"⚠️ Valor de nota inválido: {valor}")
        return None

def validar_mapeamento(resultado: dict, alunos_site: list) -> dict:
    resultado = ListaNotasMapeadas.model_validate(resultado, strict=True).model_dump()
    oficiais = {a["id_aluno"]: a["nome_oficial"] for a in alunos_site}
    if len(oficiais) != len(alunos_site):
        raise ValueError("IDs de alunos duplicados na página.")
    vistos = set()
    for aluno in resultado["alunos"]:
        identificador = aluno["id_aluno"]
        if identificador in vistos or oficiais.get(identificador) != aluno["nome_oficial"]:
            raise ValueError("A resposta contém aluno desconhecido, duplicado ou com nome divergente.")
        vistos.add(identificador)
    return resultado


def planejar_preenchimento(page, resultado: dict, alunos_site: list, bimestre: int):
    resultado = validar_mapeamento(resultado, alunos_site)
    plano = []
    for aluno in resultado["alunos"]:
        painel = page.locator(f'[id={json.dumps(aluno["id_aluno"])}]')
        for numero in range(1, 4):
            valor = aluno[f"nota{numero}"]
            if valor is None:
                continue
            sufixo = f"nt{numero}n{bimestre}"
            campo = painel.locator(f"input[class*='{sufixo}'], input[id*='{sufixo}']")
            if campo.count() != 1:
                raise ValueError(f"Campo da Nota {numero} ausente ou ambíguo; nenhum preenchimento iniciado.")
            plano.append((campo, validar_e_formatar_nota(valor), aluno["nome_oficial"], numero))
    return plano


# ----------------------------------------------------------------------
# 4. PROCESSAMENTO DE MÚLTIPLAS FOTOS VIA GEMINI
# ----------------------------------------------------------------------
def processar_cadernos_com_html(caminhos_imagens: List[str], alunos_site: list) -> dict:
    client = genai.Client()

    uploads_imagens = []
    for cam in caminhos_imagens:
        logging.info(f"📤 Fazendo upload da imagem '{cam}' para o Gemini...")
        upload_obj = client.files.upload(file=cam)
        uploads_imagens.append(upload_obj)

    prompt = f"""
    Você é um assistente especialista em lançamento de notas escolares.
    
    Abaixo está a lista oficial de alunos extraída diretamente do diário escolar no portal (HTML):
    {json.dumps(alunos_site, ensure_ascii=False, indent=2)}

    Sua tarefa:
    1. Analise TODAS as imagens do caderno enviadas juntas e identifique os nomes dos alunos e os valores presentes nas colunas 'Nota 1', 'Nota 2' e 'Nota 3'.
    2. Combine as informações das páginas se a lista de alunos continuar de uma imagem para a outra.
    3. Faça a correspondência exata entre os alunos do caderno e os alunos da lista oficial (mesmo com variações de grafia ou abreviações).
    4. Para cada aluno, extraia os valores numéricos de `nota1`, `nota2` e `nota3`. Se um aluno não tiver nota em alguma coluna, retorne null.
    5. Retorne um JSON estrito contendo o `id_aluno` oficial, o `nome_oficial` exatamente como está na lista enviada e os campos `nota1`, `nota2` e `nota3`.
    """

    logging.info(f"🤖 Processando {len(uploads_imagens)} imagem(ns) e extraindo as 3 notas por aluno...")
    
    modelo = "gemini-3.8-flash"
    max_tentativas = 5
    
    # Concatena os uploads das imagens junto com o prompt textual
    conteudo_requisicao = uploads_imagens + [prompt]
    
    for tentativa in range(1, max_tentativas + 1):
        try:
            logging.info(f"Tentativa {tentativa}/{max_tentativas} com o modelo: {modelo}")
            response = client.models.generate_content(
                model=modelo,
                contents=conteudo_requisicao,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ListaNotasMapeadas,
                    tools=[]
                ),
            )
            return validar_mapeamento(json.loads(response.text), alunos_site)
        except Exception as e:
            logging.warning(f"⚠️ Tentativa {tentativa} com {modelo} falhou: {e}")
            if ("503" in str(e) or "429" in str(e)) and tentativa < max_tentativas:
                tempo_espera = tentativa * 7
                logging.info(f"⏳ Servidor ocupado ou cota excedida. Aguardando {tempo_espera}s...")
                time.sleep(tempo_espera)
            else:
                raise RuntimeError(f"❌ Não foi possível obter resposta do Gemini após {tentativa} tentativa(s). Erro: {e}")

# ----------------------------------------------------------------------
# 5. EXECUÇÃO DA AUTOMAÇÃO NO PLAYWRIGHT
# ----------------------------------------------------------------------
def executar_em_aba_ativa(caminhos_imagens: List[str], bimestre: int = 3):
    if bimestre not in (1, 2, 3, 4):
        raise ValueError("O bimestre deve estar entre 1 e 4.")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            logging.error("❌ Não foi possível conectar ao navegador na porta 9222.")
            logging.error("Certifique-se de ter iniciado o navegador com: --remote-debugging-port=9222")
            return

        context = browser.contexts[0]
        page = context.pages[0]

        logging.info(f"📌 Conectado à página: {page.url}")

        print(f"\n👉 Certifique-se de estar na tela da turma no SIGEDUC e pressione ENTER no terminal...")
        input()

        # SELEÇÃO DO BIMESTRE VIA DROPDOWN
        try:
            logging.info(f"Alternando para o {bimestre}º Bimestre no dropdown...")
            select_elem = page.wait_for_selector("#bimestreSelect", state="attached", timeout=5000)
            if select_elem:
                page.select_option("#bimestreSelect", str(bimestre), force=True)
                page.evaluate("""
                    const select = document.getElementById('bimestreSelect');
                    if (select) {
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                """)
                page.wait_for_timeout(1000)
                if page.locator("#bimestreSelect").input_value() != str(bimestre):
                    raise ValueError("Bimestre selecionado não corresponde ao solicitado.")
                logging.info(f"Filtro do {bimestre}º Bimestre aplicado.")
        except Exception as e:
            raise RuntimeError("Não foi possível confirmar o bimestre; preenchimento cancelado.") from e

        # EXTRAÇÃO DOS ALUNOS DOS PAINÉIS RICHFACES (.rich-panel)
        logging.info("🔍 Mapeando estrutura de alunos da página HTML...")
        
        paineis = page.query_selector_all(".rich-panel")
        alunos_site = []

        for painel in paineis:
            header = painel.query_selector(".rich-panel-header")
            if not header:
                continue
            
            nome_texto = header.inner_text().strip()
            if nome_texto:
                id_aluno = painel.get_attribute("id")
                if not id_aluno:
                    raise ValueError("Painel de aluno sem ID estável; operação cancelada.")
                alunos_site.append({
                    "id_aluno": id_aluno,
                    "nome_oficial": nome_texto
                })

        logging.info(f"✅ {len(alunos_site)} alunos mapeados com sucesso na tela.")

        if not alunos_site:
            logging.error("⚠️ Nenhum aluno encontrado. Verifique se o diário de classe está aberto na tela.")
            return

        # EXTRAÇÃO DAS 3 NOTAS VIA GEMINI (MÚLTIPLAS IMAGENS)
        resultado_mapeado = processar_cadernos_com_html(caminhos_imagens, alunos_site)

        print("\n" + "="*50)
        print(f"📊 NOTAS EXTRAÍDAS E MAPEADAS ({bimestre}º BIMESTRE - NOTAS 1, 2 E 3):")
        print("="*50)
        print(json.dumps(resultado_mapeado, indent=2, ensure_ascii=False))

        plano = planejar_preenchimento(page, resultado_mapeado, alunos_site, bimestre)

        confirmar = input(f"\n⚠️ Deseja preencher todas as 3 notas no {bimestre}º Bimestre? (s/n): ")
        if confirmar.lower() != 's':
            logging.info("Operação cancelada pelo usuário.")
            return

        # INSERÇÃO DAS NOTAS NO PORTAL
        logging.info(f"⚡ Inserindo as 3 notas no portal para o {bimestre}º Bimestre...")
        sucessos = 0
        for campo, nota_fmt, nome_aluno, numero in plano:
            campo.fill(nota_fmt)
            campo.dispatch_event("change")
            campo.dispatch_event("blur")
            logging.info(f"  -> NT{numero} [{nota_fmt}] inserida para: {nome_aluno}")
            sucessos += 1

        logging.info(f"Processo concluído. Campos preenchidos: {sucessos}. Confira a gravação no portal.")


# ----------------------------------------------------------------------
# 6. PONTO DE ENTRADA DO SCRIPT
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    
    print("="*60)
    print(" LANCE-NOTAS SIGEDUC / EDUC21 (MÚLTIPLAS IMAGENS)")
    print("="*60)

    # 1. Captura uma ou mais imagens via linha de comando ou terminal
    if len(sys.argv) > 1:
        caminhos_fotos = sys.argv[1:]
    else:
        entrada = input("📷 Digite os caminhos das imagens separados por espaço (ex: foto1.jpeg foto2.jpeg): ").strip()
        caminhos_fotos = entrada.split()

    # Valida a existência dos arquivos
    fotos_validas = [f for f in caminhos_fotos if os.path.exists(f)]

    if not fotos_validas:
        print("❌ Nenhuma imagem válida encontrada nos caminhos informados.")
        sys.exit(1)

    # 2. Bimestre
    bimestre_input = input("📅 Digite o Bimestre desejado (1, 2, 3 ou 4) [Padrão: 3]: ").strip()
    bimestre_num = int(bimestre_input) if bimestre_input.isdigit() else 3

    executar_em_aba_ativa(fotos_validas, bimestre=bimestre_num)