# Referencia eTrace — fluxo da Cabine de Leitura (movimentacao)

Observado em 2026-11-07 no eTrace (Fera Palace Hotel), tela `/cabin`, usuario jucimara, movimentando 20 toalhas de piscina.

## Passo a passo

1. **Cabine de Leitura** (menu): o leitor le os itens na cabine. Cabecalho "Cabine — N Itens" (N = tags lidas).
2. **Reiniciar leitura**: botao largo; zera a leitura atual e comeca outra.
3. Aviso apos a leitura: *"Leitura concluida. Se a quantidade detectada for diferente do esperado, clique aqui para nos avisar."*
4. Abas **Identificados (N)** e **Novos**: identificados = EPC cadastrada; novos = EPC desconhecida.
5. **Lista de Itens (N)** agrupada por tipo: colunas Total de Pecas, Tipo, atributos (ex.: `75x150, 100% algodao, Branco, Buddemeyer`),
   **Ultima localizacao** (ex.: Rouparia Geral) e Detalhes (icone).
6. Acoes sobre a lista: seletor de **destino**, **Movimentar**, **Definir estado**, **Editar**.
7. **Movimentar** abre confirmacao: *"Deseja realmente registrar a movimentacao de 20 itens para o local Piscina?"* (Cancelar / Confirmar).
8. Sucesso: toast verde *"Movimentacao de itens registrada com sucesso!"* e **Detalhes da Movimentacao**: data/hora, **usuario**,
   local de destino, itens (qtd + tipo).
9. **Imprimir**: comprovante em formato de cupom: "Movimentacao de Itens para <destino>", data, **Responsavel**, tabela Quantidade | Itens,
   **Total de pecas**, linha de **Assinatura**. (Chrome > salvar como PDF/imprimir.)

## Comparacao com o Cabine RFID (o que ja temos x falta)

| eTrace | Cabine RFID |
|---|---|
| Ler cabine / identificados x novos | Feito (Registrar leitura, lote, abas) |
| Total por tipo + ultima localizacao | Parcial: falta coluna **ultima localizacao** e agrupamento por tipo |
| Reiniciar leitura | Falta (hoje e so registrar outra leitura) |
| Confirmacao antes de movimentar | Falta |
| Definir estado / Editar em lote | Falta na tela da cabine (existe no Inventario) |
| Usuario na movimentacao | Existe `usuario` em transferencias; falta exibir no detalhe |
| Detalhes da movimentacao + Imprimir comprovante com assinatura | Falta |
| "Clique aqui para nos avisar" (divergencia) | Falta (poderia disparar o alerta WhatsApp) |
