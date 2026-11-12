"""Nome de tags em massa: nomes por linha, sequenciais, importação CSV, conferência e inventário."""


def nomes(client):
    return {i["epc"]: i["nome"] for i in client.get("/api/itens?limit=50").json()}


def test_cadastro_em_lote_com_nomes_por_linha_e_sequenciais(client):
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão", "nome": "Roupão antigo"})
    r = client.post("/api/itens/cadastrar-lote", json={
        "epcs": ["AA11", "BB22", "CC33", "DD44", "EE55"], "tipo": "Lençol", "nomes": {"cc33": "Lençol do 301"},
        "prefixo": "  Lençol ", "inicio": 7, "digitos": 3}).json()
    assert r["cadastrados"] == 4 and r["proximo_numero"] == 10  # BB22, DD44, EE55 numerados; CC33 tem nome próprio
    assert nomes(client) == {"AA11": "Roupão antigo", "BB22": "Lençol 007", "CC33": "Lençol do 301",
                             "DD44": "Lençol 008", "EE55": "Lençol 009"}
    assert client.post("/api/itens/cadastrar-lote", json={"epcs": ["FF66"], "tipo": "x", "prefixo": "a", "digitos": 20}).status_code == 400


def test_importar_nomes_csv(client):
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão", "nome": "Velho"})
    client.post("/api/itens", json={"epc": "BB22", "tipo": "Roupão"})
    csv = "epc;nome\r\nAA11;Roupão 1\nbb22,Roupão 2\nCC33;Roupão 3\nxx;ruim\nDD44;\n"
    r = client.post("/api/itens/importar-nomes", json={"texto": csv, "sobrescrever": False}).json()
    assert r["nomeados"] == 1 and r["ignorados_com_nome"] == 1  # AA11 já tinha nome e não é sobrescrito
    assert r["nao_cadastrados"] == ["CC33"] and r["total_invalidos"] == 2
    assert nomes(client)["AA11"] == "Velho" and nomes(client)["BB22"] == "Roupão 2"
    r = client.post("/api/itens/importar-nomes", json={"texto": csv, "tipo": "Roupão"}).json()
    assert r["nomeados"] == 2 and r["cadastrados"] == 1
    assert nomes(client) == {"AA11": "Roupão 1", "BB22": "Roupão 2", "CC33": "Roupão 3"}
    assert client.post("/api/itens/importar-nomes", json={"texto": "  \n"}).status_code == 400


def test_nome_na_conferencia_e_no_inventario(client):
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão", "nome": "Roupão 301"})
    client.post("/api/itens", json={"epc": "BB22", "tipo": "Toalha"})
    cid = client.post("/api/conferencias", json={"filtros": {}}).json()["id"]
    itens = {i["epc"]: i["nome"] for i in client.get(f"/api/conferencias/{cid}").json()["itens"]}
    assert itens == {"AA11": "Roupão 301", "BB22": None}
    assert "Roupão 301" in client.get(f"/api/conferencias/{cid}/export.csv").text
    client.post(f"/api/conferencias/{cid}/finalizar")
    assert {i["epc"]: i["nome"] for i in client.get(f"/api/conferencias/{cid}").json()["itens"]}["AA11"] == "Roupão 301"
    inv = client.get("/api/inventario?pecas=1").json()
    assert {p["epc"]: p["nome"] for p in inv["pecas"]} == {"AA11": "Roupão 301", "BB22": None}
    assert [p["epc"] for p in client.get("/api/inventario?pecas=1&q=301").json()["pecas"]] == ["AA11"]
    assert client.get("/api/inventario").json()["pecas"] == []
