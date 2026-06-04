"""
STF — Corte Aberta: ingestor de CSVs + enriquecimento ASP
The BR Insider

Fase 1: Ingestão dos CSVs exportados manualmente do painel Dados Abertos
        https://transparencia.stf.jus.br/extensions/dados_abertos/dados_abertos.html

Fluxo:
  1. Usuário baixa CSVs manualmente da seção "Dados Abertos" do Corte Aberta
     e coloca em data/stf/raw/
  2. Este script detecta os arquivos, normaliza e faz upsert no Supabase
  3. Ao final, chama stf_refresh_matviews() para atualizar as views de tendência

Uso:
  python -m ingestao.connectors.stf --pasta data/stf/raw/
  python -m ingestao.connectors.stf --pasta data/stf/raw/ --partes   # enriquece partes via ASP
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("stf")

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# Carrega .env da raiz do projeto se as vars não estiverem no ambiente
def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v

_load_env()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

HEADERS_WEB = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "text/html,*/*",
}

ASP_BASE = "https://portal.stf.jus.br/processos"
REQUEST_DELAY = 1.5  # segundos entre requests ao ASP (ser gentil com o servidor)

# Mapeamento de nome de arquivo CSV → dataset canônico
DATASET_MAP = {
    "acervo":                      "acervo",
    "partes":                      "acervo_partes",
    "2000_a_2004":                 "decisoes_2000_2004",
    "2005_a_2009":                 "decisoes_2005_2009",
    "2010_a_2014":                 "decisoes_2010_2014",
    "2015_a_2019":                 "decisoes_2015_2019",
    "2020_a_2024":                 "decisoes_2020_2024",
    "2025":                        "decisoes_2025",
    "2026":                        "decisoes_2026",
    "temas":                       "rg_temas",
    "suspensao_nacional":          "rg_suspensao",
    "representativo":              "rg_representativo",
    "controle_concentrado":        "controle_concentrado",
    "amicus":                      "controle_concentrado",
    "acoes_covid_decisoes":        "decisoes_covid",
    "acoes_covid_processos":       "acervo_covid",
    "reclamacoes_decisoes":        "decisoes_reclamacoes",
    "reclamacoes_processos":       "acervo_reclamacoes",
    "reclamacoes_partes":          "acervo_partes",
    "informacao_a_sociedade":      "informacao_sociedade",
    "omissao_inconstitucional":    "omissao_inconstitucional",
}

# ---------------------------------------------------------------------------
# Normalização de nomes de ministros
# ---------------------------------------------------------------------------

MINISTROS_SLUG: dict[str, str] = {
    "Alexandre de Moraes":          "alexandre-de-moraes",
    "André Mendonça":               "andre-mendonca",
    "Cármen Lúcia":                 "carmen-lucia",
    "Cristiano Zanin":              "cristiano-zanin",
    "Dias Toffoli":                 "dias-toffoli",
    "Edson Fachin":                 "edson-fachin",
    "Flávio Dino":                  "flavio-dino",
    "Gilmar Mendes":                "gilmar-mendes",
    "Luiz Fux":                     "luiz-fux",
    "Nunes Marques":                "nunes-marques",
    "Roberto Barroso":              "roberto-barroso",
    # ex-ministros relevantes para histórico
    "Marco Aurélio":                "marco-aurelio",
    "Ricardo Lewandowski":          "ricardo-lewandowski",
    "Rosa Weber":                   "rosa-weber",
    "Celso de Mello":               "celso-de-mello",
    "Ayres Britto":                 "ayres-britto",
    "Joaquim Barbosa":              "joaquim-barbosa",
    "Teori Zavascki":               "teori-zavascki",
    "Eros Grau":                    "eros-grau",
    "Ellen Gracie":                 "ellen-gracie",
    "Nelson Jobim":                 "nelson-jobim",
}


def _slugify(nome: str) -> str:
    """Converte nome do ministro para slug canônico."""
    # Tenta match direto primeiro
    for nome_canônico, slug in MINISTROS_SLUG.items():
        if nome_canônico.upper() in nome.upper():
            return slug
    # Fallback: normaliza o nome recebido
    s = unicodedata.normalize("NFD", nome.lower())
    s = s.encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    # Remove prefixo "MIN." ou "MINISTRO"
    s = re.sub(r"^(min[-.]?|ministro-)", "", s)
    return s


# ---------------------------------------------------------------------------
# Normalização de resultado de decisão
# ---------------------------------------------------------------------------

RESULTADO_MAPA: dict[str, str] = {
    # Favorável ao requerente/recorrente
    "procedente":                               "favoravel",
    "procedente em parte":                      "favoravel",
    "provido":                                  "favoravel",
    "provido em parte":                         "favoravel",
    "deferido":                                 "favoravel",
    "deferido em parte":                        "favoravel",
    "concedida":                                "favoravel",
    "concedido":                                "favoravel",
    "conhecido e provido":                      "favoravel",
    "agravo regimental provido":                "favoravel",
    "agravo provido e desde logo provido":      "favoravel",
    # Contrário
    "improcedente":                             "contrario",
    "não provido":                              "contrario",
    "desprovido":                               "contrario",
    "indeferido":                               "contrario",
    "negado":                                   "contrario",
    "negado seguimento":                        "contrario",
    "não conhecido":                            "contrario",
    "não conhecidos":                           "contrario",
    "não conhecida":                            "contrario",
    "agravo não provido":                       "contrario",
    "agravo regimental não provido":            "contrario",
    "agravo regimental não conhecido":          "contrario",
    "embargos rejeitados":                      "contrario",
    "embargos não conhecidos":                  "contrario",
    "inadmitidos os embargos":                  "contrario",
    # Neutro / processual
    "prejudicado":                              "neutro",
    "homologado":           "neutro",
    "arquivado":            "neutro",
    "baixado":              "neutro",
    "convertido":           "neutro",
    "remetido":             "neutro",
}


def _normalizar_resultado(nome_decisao: str) -> Optional[str]:
    if not nome_decisao:
        return None
    nd = nome_decisao.lower().strip()
    for termo, resultado in RESULTADO_MAPA.items():
        if termo in nd:
            return resultado
    return None


# ---------------------------------------------------------------------------
# Parsing de datas
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> Optional[date]:
    if not s or not s.strip():
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Tipo de decisão
# ---------------------------------------------------------------------------

def _tipo_decisao(orgao: str, nome: str) -> str:
    orgao_l = (orgao or "").lower()
    nome_l = (nome or "").lower()
    if "plenário" in orgao_l or "turma" in orgao_l:
        if "acórdão" in nome_l or "julgamento" in nome_l:
            return "acórdão"
        return "colegiada"
    return "monocrática"


# ---------------------------------------------------------------------------
# Detecção de dataset a partir do nome do arquivo
# ---------------------------------------------------------------------------

def _detectar_dataset(path: Path) -> Optional[str]:
    stem = path.stem.lower()
    for chave, dataset in DATASET_MAP.items():
        if chave in stem:
            return dataset
    return None


# ---------------------------------------------------------------------------
# Hash do arquivo para log de ingestão
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Cliente Supabase mínimo (REST)
# ---------------------------------------------------------------------------

class SupabaseClient:
    def __init__(self) -> None:
        self.base = SUPABASE_URL.rstrip("/") + "/rest/v1"
        self.headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        self.session = requests.Session()

    def upsert(self, tabela: str, registros: list[dict]) -> int:
        """Upsert em lote. Retorna nº de linhas inseridas/atualizadas."""
        if not registros:
            return 0
        LOTE = 500
        total = 0
        for i in range(0, len(registros), LOTE):
            lote = registros[i : i + LOTE]
            resp = self.session.post(
                f"{self.base}/{tabela}",
                json=lote,
                headers=self.headers,
            )
            if not resp.ok:
                raise ValueError(f"Supabase {resp.status_code}: {resp.text[:400]}")
            total += len(lote)
        return total

    def rpc(self, funcao: str, params: dict = {}) -> None:
        resp = self.session.post(
            f"{self.base.replace('/rest/v1', '')}/rest/v1/rpc/{funcao}",
            json=params,
            headers=self.headers,
        )
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Ingestão de CSV de DECISÕES
# ---------------------------------------------------------------------------

def ingerir_decisoes(path: Path, dataset: str, db: SupabaseClient) -> tuple[int, int]:
    """Retorna (ok, erros)."""
    ok = erros = 0
    registros: list[dict] = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        # Detectar delimitador (vírgula ou ponto-e-vírgula)
        amostra = f.read(4096)
        f.seek(0)
        delim = ";" if amostra.count(";") > amostra.count(",") else ","
        reader = csv.DictReader(f, delimiter=delim)

        for linha in reader:
            try:
                # Normalizar nomes de colunas (lower + strip)
                row = {k.lower().strip(): v.strip() for k, v in linha.items() if k}

                # Extrair campos — CSVs do Corte Aberta usam nomes específicos:
                # "idfatodecisao", "nome ministro (a)", "andamento decisão", "órgão julgador"
                # Suportamos também variantes legadas
                incidente    = int(row.get("idfatodecisao") or row.get("incidente") or 0) or None

                # Classe e número: extrair do campo "processo" (ex: "ADI 7236")
                processo_raw = row.get("processo") or ""
                partes_proc  = processo_raw.strip().split()
                classe       = row.get("classe") or (partes_proc[0] if partes_proc else None)
                numero       = row.get("número") or row.get("numero") or (partes_proc[1] if len(partes_proc) > 1 else None)
                num_int      = int(re.sub(r"\D", "", numero)) if numero else None

                ano_aut      = int(row.get("ano da decisão") or row.get("ano de autuação") or row.get("ano_autuacao") or 0) or None
                data_dec_s   = row.get("data da decisão") or row.get("data_decisao") or ""

                # Ministro: campo real é "nome ministro (a)"
                ministro     = (
                    row.get("nome ministro (a)") or
                    row.get("ministro") or
                    row.get("relator") or
                    row.get("relator atual") or ""
                ).strip()

                # Órgão julgador: campo real é "órgão julgador"
                orgao        = (row.get("órgão julgador") or row.get("orgao_julgador") or "").strip()

                # Nome/tipo da decisão: campo real é "andamento decisão"
                nome_dec     = (
                    row.get("andamento decisão") or
                    row.get("tipo decisão") or
                    row.get("decisão") or
                    row.get("tipo de decisão") or
                    row.get("nome_decisao") or
                    row.get("descrição") or ""
                ).strip()

                # Tipo de decisão: derivado de "origem decisão" (MONOCRÁTICA, TRIBUNAL PLENO etc.)
                origem_dec   = (row.get("origem decisão") or "").strip()
                assunto      = row.get("ramo direito") or row.get("assunto") or row.get("ramo do direito") or None
                requerente   = row.get("requerente") or row.get("parte ativa") or None

                data_dec = _parse_date(data_dec_s)
                ministro_id = _slugify(ministro) if ministro else None
                # usar origem_dec (MONOCRÁTICA / TRIBUNAL PLENO / 1ª TURMA etc.) para tipo
                tipo_dec    = _tipo_decisao(origem_dec or orgao, nome_dec)
                resultado   = _normalizar_resultado(nome_dec)

                reg = {
                    "incidente":        incidente,
                    "numero_processo":  f"{classe} {numero}".strip() if classe and numero else None,
                    "classe":           classe,
                    "numero":           num_int,
                    "ano_autuacao":     ano_aut,
                    "data_decisao":     data_dec.isoformat() if data_dec else None,
                    "tipo_decisao":     tipo_dec,
                    "nome_decisao":     nome_dec or None,
                    "ministro":         ministro or None,
                    "ministro_id":      ministro_id,
                    "orgao_julgador":   orgao or None,
                    "resultado":        resultado,
                    "assunto":          assunto,
                    "requerente":       requerente,
                    "fonte_csv":        dataset,
                }
                # Manter todas as chaves (PostgREST PGRST102 exige chaves uniformes no lote)
                registros.append(reg)
                ok += 1

            except Exception as e:
                logger.warning(f"Erro linha {ok + erros + 1}: {e}")
                erros += 1

    if registros:
        db.upsert("stf_decisoes", registros)

    return ok, erros


# ---------------------------------------------------------------------------
# Ingestão de CSV de ACERVO
# ---------------------------------------------------------------------------

def ingerir_acervo(path: Path, dataset: str, db: SupabaseClient) -> tuple[int, int]:
    ok = erros = 0
    registros: list[dict] = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        amostra = f.read(4096); f.seek(0)
        delim = ";" if amostra.count(";") > amostra.count(",") else ","
        reader = csv.DictReader(f, delimiter=delim)

        for linha in reader:
            try:
                row = {k.lower().strip(): v.strip() for k, v in linha.items() if k}

                incidente   = int(row.get("incidente") or 0) or None
                classe      = row.get("classe") or None
                numero      = row.get("número") or row.get("numero") or None
                num_int     = int(re.sub(r"\D", "", numero)) if numero else None
                ano_aut     = int(row.get("ano de autuação") or row.get("ano_autuacao") or 0) or None
                relator     = (row.get("relator") or row.get("ministro relator") or "").strip()
                situacao    = row.get("situação") or row.get("situacao") or None
                origem      = row.get("origem") or row.get("tribunal de origem") or None
                assunto     = row.get("assunto") or row.get("ramo do direito") or None
                data_aut_s  = row.get("data de autuação") or row.get("data_autuacao") or ""
                requerente  = row.get("requerente") or row.get("parte ativa") or None
                requerido   = row.get("requerido") or row.get("parte passiva") or None

                reg = {
                    "incidente":        incidente,
                    "numero_processo":  f"{classe} {numero}".strip() if classe and numero else None,
                    "classe":           classe,
                    "numero":           num_int,
                    "ano_autuacao":     ano_aut,
                    "data_autuacao":    _parse_date(data_aut_s).isoformat() if _parse_date(data_aut_s) else None,
                    "ministro_relator": relator or None,
                    "ministro_relator_id": _slugify(relator) if relator else None,
                    "situacao":         situacao,
                    "origem":           origem,
                    "assunto_principal": assunto,
                    "requerente":       requerente,
                    "requerido":        requerido,
                    "fonte_csv":        dataset,
                }
                registros.append(reg)
                ok += 1

            except Exception as e:
                logger.warning(f"Erro linha {ok + erros + 1}: {e}")
                erros += 1

    if registros:
        db.upsert("stf_processos", registros)

    return ok, erros


# ---------------------------------------------------------------------------
# Enriquecimento de Partes via ASP (Fase 2 — opcional, --partes)
# ---------------------------------------------------------------------------

def enriquecer_partes(incidentes: list[int], db: SupabaseClient) -> int:
    """
    Para cada incidente, consulta abaPartes.asp e salva em stf_partes.
    Usa delay de REQUEST_DELAY segundos entre requests.
    """
    total = 0
    sess = requests.Session()

    for i, incidente in enumerate(incidentes, 1):
        try:
            url = f"{ASP_BASE}/abaPartes.asp?incidente={incidente}"
            resp = sess.get(url, headers=HEADERS_WEB, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            registros: list[dict] = []

            for bloco in soup.select(".processo-partes"):
                polo_el  = bloco.select_one(".detalhe-parte")
                nome_el  = bloco.select_one(".nome-parte")
                polo  = polo_el.get_text(strip=True) if polo_el else None
                nome  = nome_el.get_text(strip=True) if nome_el else None
                if polo and nome:
                    registros.append({
                        "incidente": incidente,
                        "polo": polo,
                        "nome": nome,
                    })

            if registros:
                db.upsert("stf_partes", registros)
                total += len(registros)

            if i % 50 == 0:
                logger.info(f"Partes: {i}/{len(incidentes)} processos processados ({total} partes)")

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            logger.warning(f"Erro partes incidente {incidente}: {e}")

    return total


# ---------------------------------------------------------------------------
# CLI principal
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Ingestor STF Corte Aberta — CSVs + ASP enrichment"
    )
    parser.add_argument(
        "--pasta", default="data/stf/raw",
        help="Pasta com os CSVs baixados do Corte Aberta"
    )
    parser.add_argument(
        "--partes", action="store_true",
        help="Enriquecer partes via abaPartes.asp após ingestão de acervo"
    )
    parser.add_argument(
        "--refresh", action="store_true", default=True,
        help="Executar stf_refresh_matviews() ao final (padrão: True)"
    )
    args = parser.parse_args()

    pasta = Path(args.pasta)
    if not pasta.exists():
        logger.error(f"Pasta não encontrada: {pasta}")
        return

    db = SupabaseClient()
    csvs = sorted(pasta.glob("*.csv"))
    logger.info(f"Encontrados {len(csvs)} CSVs em {pasta}")

    total_ok = total_erros = 0

    for csv_path in csvs:
        dataset = _detectar_dataset(csv_path)
        if not dataset:
            logger.warning(f"Dataset não reconhecido: {csv_path.name} — pulando")
            continue

        arquivo_hash = _sha256(csv_path)

        # Verificar se já foi ingerido (mesmo hash)
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/stf_ingestao_log",
            params={"dataset": f"eq.{dataset}", "arquivo_hash": f"eq.{arquivo_hash}"},
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        )
        if resp.ok and resp.json():
            logger.info(f"Já ingerido (mesmo hash): {csv_path.name} — pulando")
            continue

        logger.info(f"Ingerindo: {csv_path.name} → dataset={dataset}")

        DATASETS_DECISOES = {
            "decisoes_2000_2004", "decisoes_2005_2009", "decisoes_2010_2014",
            "decisoes_2015_2019", "decisoes_2020_2024", "decisoes_2025", "decisoes_2026",
            "decisoes_covid", "decisoes_reclamacoes",
        }
        DATASETS_ACERVO = {
            "acervo", "controle_concentrado", "acervo_covid", "acervo_reclamacoes",
        }
        DATASETS_IGNORAR = {
            "rg_temas", "rg_suspensao", "rg_representativo", "acervo_partes",
            "controle_concentrado_acervo", "informacao_sociedade", "omissao_inconstitucional",
        }

        if dataset in DATASETS_DECISOES or "decisoes" in dataset:
            ok, erros = ingerir_decisoes(csv_path, dataset, db)
        elif dataset in DATASETS_ACERVO:
            ok, erros = ingerir_acervo(csv_path, dataset, db)
        elif dataset in DATASETS_IGNORAR:
            logger.info(f"Dataset {dataset} sem tabela dedicada ainda — pulando")
            continue
        else:
            logger.info(f"Dataset {dataset} sem handler — pulando")
            continue

        total_ok += ok
        total_erros += erros

        # Log de ingestão
        db.upsert("stf_ingestao_log", [{
            "dataset":      dataset,
            "linhas_raw":   ok + erros,
            "linhas_ok":    ok,
            "linhas_erro":  erros,
            "arquivo_hash": arquivo_hash,
        }])
        logger.info(f"  → {ok} ok, {erros} erros")

    # Enriquecimento de partes
    if args.partes:
        logger.info("Buscando incidentes para enriquecimento de partes...")
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/stf_processos",
            params={"select": "incidente", "incidente": "not.is.null", "limit": "5000"},
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        )
        if resp.ok:
            incidentes = [r["incidente"] for r in resp.json() if r.get("incidente")]
            logger.info(f"Enriquecendo partes de {len(incidentes)} processos...")
            total_partes = enriquecer_partes(incidentes, db)
            logger.info(f"Partes inseridas: {total_partes}")

    # Refresh das matviews de tendência
    if args.refresh and (total_ok > 0):
        logger.info("Atualizando matviews de tendência...")
        try:
            db.rpc("stf_refresh_matviews")
            logger.info("Matviews atualizadas com sucesso.")
        except Exception as e:
            logger.warning(f"Erro no refresh das matviews: {e}")

    logger.info(f"Concluído. Total: {total_ok} linhas ingeridas, {total_erros} erros.")


if __name__ == "__main__":
    main()
