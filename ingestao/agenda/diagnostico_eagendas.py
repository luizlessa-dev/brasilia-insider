"""
Diagnóstico e-Agendas: inspeciona os cargos disponíveis para PR e
os 8 ministérios que não retornaram dados no backfill.

Uso:
  EAGENDAS_TOKEN=xxx python -m ingestao.agenda.diagnostico_eagendas

Saída:
  Para cada órgão, lista TODOS os cargos (sem filtro de topo),
  para identificar qual keyword está faltando.
"""
from __future__ import annotations

import os
import sys
import json
import time
import requests

BASE_URL = "https://eagendas.cgu.gov.br/api/v2"

# Órgãos a diagnosticar: PR + os 8 sem dados
ORGAOS_DIAGNOSTICO = {
    511:  "PR (Presidência da República)",
    638:  "VPR (Vice-Presidência)",
    1397: "MPI",
    1405: "MIR",
    1407: "MDA",
    1424: "MMULHERES",
    1419: "MAPA",
    862:  "MCTI",
    1386: "MDHC",
    1393: "MIDR",
}


def main():
    token = os.environ.get("EAGENDAS_TOKEN", "")
    if not token:
        print("ERRO: EAGENDAS_TOKEN não definido", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "BRInsider-Diagnostico/1.0 (contato@thebrinsider.com)",
    })

    for orgao_id, nome in ORGAOS_DIAGNOSTICO.items():
        print(f"\n{'='*60}")
        print(f"ÓRGÃO: {nome} (id={orgao_id})")
        print('='*60)

        try:
            time.sleep(0.5)
            resp = session.get(
                f"{BASE_URL}/cargos-comissionados",
                params={"orgao_id": orgao_id, "situacao": "Ativo", "per_page": 200},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("sucesso"):
                print(f"  ⚠ API retornou sucesso=false: {data.get('mensagem', '')}")
                continue

            cargos = data.get("resposta", {}).get("cargos_comissionados", [])
            print(f"  Total de cargos ativos: {len(cargos)}")

            if not cargos:
                print("  ⛔ Nenhum cargo ativo retornado.")
                continue

            # Mostrar todos os cargos (sem filtro)
            for c in cargos:
                print(f"  id={c.get('id'):>6}  desc={c.get('descricao')}")

            # Também testar endpoint de compromissos com o primeiro cargo
            if cargos:
                cargo_id = cargos[0]["id"]
                time.sleep(0.5)
                resp2 = session.get(
                    f"{BASE_URL}/compromissos",
                    params={
                        "orgao_id": orgao_id,
                        "cargo_comissao_id": cargo_id,
                        "data_inicio": "01-06-2026",
                        "data_termino": "08-06-2026",
                        "per_page": 5,
                    },
                    timeout=30,
                )
                resp2.raise_for_status()
                data2 = resp2.json()
                comps = data2.get("resposta", {}).get("compromissos", []) if data2.get("sucesso") else []
                print(f"\n  → Teste compromissos (cargo {cargo_id}, 01-08/jun): {len(comps)} registros")
                for cp in comps[:3]:
                    print(f"     • {cp.get('data_inicio')} {cp.get('hora_inicio')} | {cp.get('assunto', '')[:60]}")

        except Exception as e:
            print(f"  ❌ Erro: {e}")


if __name__ == "__main__":
    main()
