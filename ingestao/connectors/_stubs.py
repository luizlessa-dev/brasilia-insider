"""
Stubs para as 25 assembleias ainda sem conector implementado.
Cada classe já está registrada no registry com metadados corretos.
Para implementar: copie a classe para seu próprio arquivo e substitua StubConnector por BaseConnector.

Ordem sugerida de implementação:
  Sprint 2 (XML/CSV): alesp, alepe, alece
  Sprint 3 (API menor): alego, ales, almt, alems, alern, alpb, alese, alal, alepi, alero, aleam, alesc
  Sprint 4 (scraping):  alerj, alba, alergs, alepa, aleto, alerr, aleac, alap
  Sprint 5 (difícil):   alema, cldf
"""
from ..base_connector import StubConnector


# ALESP foi implementada — ver connectors/alesp.py


class ALERJConnector(StubConnector):
    """Assembleia Legislativa do Rio de Janeiro — Tier 3 (scraping)"""
    assembly_id = "alerj"
    assembly_name = "Assembleia Legislativa do Estado do Rio de Janeiro"
    uf = "RJ"
    base_url = "https://www.alerj.rj.gov.br"


class ALBAConnector(StubConnector):
    """Assembleia Legislativa da Bahia — Tier 3 (scraping)"""
    assembly_id = "alba"
    assembly_name = "Assembleia Legislativa da Bahia"
    uf = "BA"
    base_url = "https://www.al.ba.gov.br"


class ALEPEConnector(StubConnector):
    """Assembleia Legislativa de Pernambuco — Tier 1 (API + CSV)"""
    assembly_id = "alepe"
    assembly_name = "Assembleia Legislativa de Pernambuco"
    uf = "PE"
    base_url = "https://www.alepe.pe.gov.br"
    # Dados abertos: https://www.alepe.pe.gov.br/dadosabertos/


class ALERGSConnector(StubConnector):
    """Assembleia Legislativa do Rio Grande do Sul — Tier 3 (scraping)"""
    assembly_id = "alergs"
    assembly_name = "Assembleia Legislativa do Rio Grande do Sul"
    uf = "RS"
    base_url = "https://www.al.rs.gov.br"


class ALECEConnector(StubConnector):
    """Assembleia Legislativa do Ceará — Tier 2 (CSV misto)"""
    assembly_id = "alece"
    assembly_name = "Assembleia Legislativa do Ceará"
    uf = "CE"
    base_url = "https://www.al.ce.gov.br"


class ALEPAConnector(StubConnector):
    """Assembleia Legislativa do Pará — Tier 3 (scraping básico)"""
    assembly_id = "alepa"
    assembly_name = "Assembleia Legislativa do Pará"
    uf = "PA"
    base_url = "https://www.alepa.pa.gov.br"


class ALEMAConnector(StubConnector):
    """Assembleia Legislativa do Maranhão — Tier 4 (declaradamente fechada)"""
    assembly_id = "alema"
    assembly_name = "Assembleia Legislativa do Maranhão"
    uf = "MA"
    base_url = "https://www.al.ma.leg.br"
    # ATENÇÃO: em 2025, ALEMA declarou explicitamente não oferecer acesso automatizado.
    # Qualquer implementação aqui é scraping com risco jurídico.


class ALEGOConnector(StubConnector):
    """Assembleia Legislativa de Goiás — Tier 2"""
    assembly_id = "alego"
    assembly_name = "Assembleia Legislativa de Goiás"
    uf = "GO"
    base_url = "https://www.al.go.leg.br"


class ALESConnector(StubConnector):
    """Assembleia Legislativa do Espírito Santo — Tier 2"""
    assembly_id = "ales"
    assembly_name = "Assembleia Legislativa do Espírito Santo"
    uf = "ES"
    base_url = "https://www.al.es.gov.br"


class ALMTConnector(StubConnector):
    """Assembleia Legislativa de Mato Grosso — Tier 2"""
    assembly_id = "almt"
    assembly_name = "Assembleia Legislativa de Mato Grosso"
    uf = "MT"
    base_url = "https://www.al.mt.gov.br"


class ALEMSConnector(StubConnector):
    """Assembleia Legislativa de Mato Grosso do Sul — Tier 2"""
    assembly_id = "alems"
    assembly_name = "Assembleia Legislativa de Mato Grosso do Sul"
    uf = "MS"
    base_url = "https://www.al.ms.gov.br"


class ALERNConnector(StubConnector):
    """Assembleia Legislativa do Rio Grande do Norte — Tier 2"""
    assembly_id = "alern"
    assembly_name = "Assembleia Legislativa do Rio Grande do Norte"
    uf = "RN"
    base_url = "https://www.al.rn.leg.br"


class ALPBConnector(StubConnector):
    """Assembleia Legislativa da Paraíba — Tier 2"""
    assembly_id = "alpb"
    assembly_name = "Assembleia Legislativa da Paraíba"
    uf = "PB"
    base_url = "https://www.alpb.pb.gov.br"


class ALESEConnector(StubConnector):
    """Assembleia Legislativa de Sergipe — Tier 2"""
    assembly_id = "alese"
    assembly_name = "Assembleia Legislativa de Sergipe"
    uf = "SE"
    base_url = "https://www.alese.se.gov.br"


class ALALConnector(StubConnector):
    """Assembleia Legislativa de Alagoas — Tier 2/3"""
    assembly_id = "alal"
    assembly_name = "Assembleia Legislativa de Alagoas"
    uf = "AL"
    base_url = "https://www.al.al.leg.br"


class ALEPIConnector(StubConnector):
    """Assembleia Legislativa do Piauí — Tier 3"""
    assembly_id = "alepi"
    assembly_name = "Assembleia Legislativa do Piauí"
    uf = "PI"
    base_url = "https://www.alepi.pi.gov.br"


class ALEROConnector(StubConnector):
    """Assembleia Legislativa de Rondônia — Tier 2"""
    assembly_id = "alero"
    assembly_name = "Assembleia Legislativa de Rondônia"
    uf = "RO"
    base_url = "https://www.ale.ro.leg.br"


class ALEAMConnector(StubConnector):
    """Assembleia Legislativa do Amazonas — Tier 2/3"""
    assembly_id = "aleam"
    assembly_name = "Assembleia Legislativa do Amazonas"
    uf = "AM"
    base_url = "https://www.ale.am.gov.br"


class ALESCConnector(StubConnector):
    """Assembleia Legislativa de Santa Catarina — Tier 2"""
    assembly_id = "alesc"
    assembly_name = "Assembleia Legislativa de Santa Catarina"
    uf = "SC"
    base_url = "https://www.alesc.sc.gov.br"


class ALETOConnector(StubConnector):
    """Assembleia Legislativa do Tocantins — Tier 3"""
    assembly_id = "aleto"
    assembly_name = "Assembleia Legislativa do Tocantins"
    uf = "TO"
    base_url = "https://www.al.to.leg.br"


class ALERRConnector(StubConnector):
    """Assembleia Legislativa de Roraima — Tier 3"""
    assembly_id = "alerr"
    assembly_name = "Assembleia Legislativa de Roraima"
    uf = "RR"
    base_url = "https://www.ale.rr.gov.br"


class ALEACConnector(StubConnector):
    """Assembleia Legislativa do Acre — Tier 3"""
    assembly_id = "aleac"
    assembly_name = "Assembleia Legislativa do Acre"
    uf = "AC"
    base_url = "https://www.al.ac.leg.br"


class ALAPConnector(StubConnector):
    """Assembleia Legislativa do Amapá — Tier 3"""
    assembly_id = "alap"
    assembly_name = "Assembleia Legislativa do Amapá"
    uf = "AP"
    base_url = "https://www.al.ap.gov.br"


# CLDF foi implementada — ver connectors/cldf.py
