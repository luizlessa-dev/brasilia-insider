"""
Runner CLI — dossiê completo de parlamentar.

Uso:
    python -m dossie.parlamentar_runner --cpf 11701442680
    python -m dossie.parlamentar_runner --nome "Nikolas Ferreira"
    python -m dossie.parlamentar_runner --cpf 11701442680 --json nikolas.json

Variáveis de ambiente:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

_repo_root = Path(__file__).parent.parent
if (_repo_root / ".env").exists():
    load_dotenv(_repo_root / ".env")

from .parlamentar import DossieParlamentar  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Dossiê de parlamentar")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cpf", metavar="CPF", help="CPF do parlamentar (só dígitos)")
    group.add_argument("--nome", metavar="NOME", help="Nome (ou fragmento) do parlamentar")
    parser.add_argument("--json", metavar="ARQUIVO", help="Salvar resultado em JSON")
    args = parser.parse_args()

    dossie = DossieParlamentar.from_env()

    try:
        rel = dossie.gerar(cpf=args.cpf, nome=args.nome)
    except LookupError as e:
        print(f"Erro: {e}")
        sys.exit(1)

    DossieParlamentar.imprimir(rel)

    if args.json:
        path = Path(args.json)
        path.write_text(
            json.dumps(DossieParlamentar.para_dict(rel),
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"JSON salvo em {path}")


if __name__ == "__main__":
    main()
