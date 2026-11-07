"""Cadastra os usuários do eTrace do Fera Palace vistos nas telas de Conferência de Estoque e Movimentações
(lidos em 2026-11-07): jucimara, Artur, Daniela, Supervisoras, SG.

  DATABASE_URL=postgresql://... python3 scripts/seed_usuarios_fera.py

- Entram como `operador`, ATIVOS e SEM SENHA: ninguém consegue logar até um admin definir a senha na
  tela Usuários (não há como saber as senhas do eTrace e não inventamos nenhuma).
- Idempotente: quem já existe não é alterado.
"""
import os, time
import psycopg

USUARIOS = [("jucimara", "Jucimara"), ("artur", "Artur"), ("daniela", "Daniela"),
            ("supervisoras", "Supervisoras"), ("sg", "SG")]
with psycopg.connect(os.environ["DATABASE_URL"]) as con:
    for login, nome in USUARIOS:
        con.execute("""INSERT INTO usuarios(usuario,nome,senha_hash,papel,ativo,criado_em) VALUES(%s,%s,NULL,'operador',1,%s)
                       ON CONFLICT(usuario) DO NOTHING""", (login, nome, time.time()))
    print("usuarios:", [r[0] for r in con.execute("SELECT usuario FROM usuarios ORDER BY usuario")])
