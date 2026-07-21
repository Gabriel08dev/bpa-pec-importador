# Importador CEAE — PEC e BPA

Aplicação local para Windows que importa atendimentos do CEAE, consulta o PEC e-SUS em modo somente leitura, revisa divergências e inclui somente registros novos no Firebird 1.5 do BPA Magnético. A remessa final continua sendo gerada pelo próprio BPA, depois da consistência.

## Tecnologia escolhida

Python 3.12 foi escolhido porque reúne, no mesmo runtime para Windows, Playwright com Microsoft Edge, leitura de Excel, PDF, OCR opcional, acesso ao Gerenciador de Credenciais e uma interface HTTP local. FastAPI atende apenas em `127.0.0.1`, numa porta aleatória e com token secreto por execução. Os binários `isql` e `gbak` do Firebird 1.5 já validados neste computador são usados como `fb_query.exe` e `fb_backup.exe`; isso evita depender de um driver moderno incompatível com a base legada.

## Instalação e abertura

1. Abra PowerShell uma única vez na pasta do projeto.
2. Copie `data/config.example.json` para `data/config.json` e ajuste os caminhos e a URL do seu ambiente.
3. Copie `data/profissionais.example.json` para `data/profissionais.json` e cadastre localmente os profissionais autorizados.
4. Coloque em `firebird/bin/` os clientes legados compatíveis descritos no arquivo dessa pasta.
5. Execute `powershell -ExecutionPolicy Bypass -File .\scripts\instalar.ps1`.
6. Use o atalho **Importador CEAE — PEC e BPA** na Área de Trabalho. Não é necessário abrir terminal nas execuções normais.
7. Na primeira utilização, informe a credencial Firebird e, opcionalmente, a credencial PEC. Elas são armazenadas pelo Windows Credential Manager, nunca em JSON ou log.

Também é possível usar `ABRIR_IMPORTADOR.cmd`. Para instalar OCR: `powershell -ExecutionPolicy Bypass -File .\scripts\instalar.ps1 -ComOCR`.

## Fluxo de uso

1. Baixe o modelo Excel no painel ou escolha um `.xlsx`, `.csv` ou `.tsv` pelo seletor do Windows.
2. Informe explicitamente a competência `AAAAMM` e importe.
3. Revise linhas, profissionais, datas, CPFs, nascimentos e CIDs. A planilha serve para pesquisa; nome e dados finais vêm do PEC.
4. Clique em **Incluir todos para consultar no PEC**. Faça login manualmente se necessário. O robô usa apenas `/cidadao`, a base local e o botão **Visualizar**; não usa “Atualizar cadastro” nem pesquisa nacional.
5. Resultado zero vira `IGNORADO_PEC`; múltiplos resultados ou divergência viram `AGUARDANDO_CONFIRMACAO`; falha individual vira `ERRO_TECNICO` e os demais continuam.
6. Gere o PDF de divergências e o lote final. Itens aguardando confirmação nunca entram na prévia de gravação.
7. Gere a prévia Firebird. O sistema lê a base ativa, valida CIDs, vínculos, procedimentos, duplicidades, folhas e sequências.
8. Revise competência, profissional, data, folha e sequência. Digite exatamente `ADICIONAR`.
9. A aplicação verifica se `bpamag.exe` está fechado, reabre e revalida o banco, recalcula a prévia, cria e valida um `.fbk`, executa uma transação com um único `COMMIT` e verifica cada inclusão.
10. Abra o BPA Magnético, execute a consistência e exporte pelo próprio BPA. A aprovação final depende da consistência do BPA, da exportação e do processamento pelo gestor/SIA.

## Regras implementadas

- CPF é sempre texto, recebe zeros à esquerda até 11 dígitos e é exibido formatado; CPF e CNS são mascarados em logs e relatórios gerais.
- Duplicidade real é somente a mesma identidade, profissional e data. O mesmo paciente pode aparecer com profissionais ou datas diferentes.
- Cada profissional tem sua sequência própria. Datas são ordenadas; até 20 pacientes ocupam a folha; a 21ª linha abre outra; datas e profissionais nunca são misturados.
- Folhas existentes não são renumeradas. Uma sequência livre da mesma data é preenchida; data nova usa a próxima folha depois da maior existente.
- Profissional é identificado pelo CNS. O nome aceita acentos, preposições e abreviações apenas na importação.
- Médicos usam `0301010072`; demais profissionais usam `0301010048`. Um profissional marcado localmente com `create_if_missing` pode ter seu vínculo criado com `INSERT ... WHERE NOT EXISTS` na primeira gravação que precisar dele.
- CID é opcional, perde o ponto e é consultado em `S_CID`. CID múltiplo, ilegível ou ausente na tabela fica vazio com aviso.
- CADCNS é localizado primeiro por CNS, depois CPF e nascimento, com escolha determinística pelo maior `ID_CADCNS`. Grafia diferente não bloqueia identidade confirmada. Cadastro antigo nunca é alterado.
- Nome, mãe, logradouro, número, complemento, bairro e e-mail respeitam os limites observados do banco. O valor completo fica na revisão e o ajuste legado gera aviso.
- Tipo de logradouro usa `081`; Brasileira vira `010`; Carangola vira IBGE `311330`, sem aplicar esse IBGE a outra cidade.
- O SQL de lote é CP1252, termina com quebra de linha, contém somente `INSERT` e consultas, usa `NOT EXISTS`, transação explícita e um único `COMMIT`. `UPDATE`, `DELETE` e alterações de esquema são recusados pelo executor.
- Retry lê novamente o banco, classifica itens já existentes e tenta somente os pendentes.

## Arquivos do lote

Cada lote fica em `outputs/<id-do-lote>/` e pode conter `revisao_planilha.json`, `revisao_final.json`, `lote_revisado.json`, `previa_firebird.json`, `relatorio_divergencias.pdf`, `relatorio_ignorados.csv`, `auditoria_importacao.json` e `resultado_gravacao.json`. SHA-256 protege a planilha e cada revisão; alteração invalida a prévia.

Os backups ficam em `backups/BPAMAG_ANTES_IMPORTACAO_AAAAMMDD_HHMMSS.fbk`. A aplicação nunca restaura backup automaticamente.

## Segurança e LGPD

- Servidor vinculado exclusivamente a `127.0.0.1`, porta aleatória, token de 256 bits, CSP, `Cache-Control: no-store`, `X-Frame-Options: DENY` e `Referrer-Policy: no-referrer`.
- Senhas ficam no Credential Manager nos alvos `BPA_PEC_IMPORTADOR_FIREBIRD` e `BPA_PEC_IMPORTADOR_PEC` e chegam ao Firebird somente por ambiente do subprocesso.
- Nenhuma senha é passada na linha de comando, arquivo SQL, JSON ou log.
- O perfil persistente do Edge fica em `.pec_profile`; restrinja o acesso ao usuário do Windows e encerre a sessão ao trocar de operador.
- Os testes usam somente identidades sintéticas. Não copie artefatos de lote para serviços de nuvem ou canais não autorizados.

## Testes

Execute `powershell -ExecutionPolicy Bypass -File .\scripts\testar.ps1`.

O teste Firebird controlado é opt-in e exige o BPA fechado:

```powershell
$env:RUN_FIREBIRD_ROLLBACK='ADICIONAR'
.\scripts\testar.ps1
Remove-Item Env:RUN_FIREBIRD_ROLLBACK
```

Ele insere um profissional sintético dentro de uma transação, confirma sua existência ali, executa `ROLLBACK` e confirma ausência posterior. Nunca deixa paciente ou produção no banco.

## Estrutura

```text
bpa_pec_importador_v2/
  app/
    main.py, config.py, models.py, security.py, credentials.py, logging_safe.py
    services/        # planilha, PEC, OCR, pacientes, profissionais, CID, folhas, lote, relatório
    repositories/    # revisão, auditoria e consultas Firebird
    firebird/         # runner 1.5, metadados, SQL, backup e verificação
    web/              # rotas, templates e CSS
  data/               # exemplos públicos; configuração e profissionais reais ficam somente no computador
  firebird/bin/       # instruções para dependências legadas instaladas localmente
  outputs/            # artefatos locais por lote
  backups/            # backups .fbk
  tests/              # unitários, regressão, interface e integração rollback
  scripts/            # instalar, executar, testar e criar atalho
  requirements.txt, requirements-ocr.txt, pyproject.toml
  ABRIR_IMPORTADOR.cmd
```

## Inspeção deste ambiente e fontes oficiais

A inspeção somente leitura confirmou `CADCNS`, `CADMED`, `CADMED_CBO_CNES`, `S_PRD` e `S_CID`, 91 colunas relevantes, `CADMED_NOME` com 30 caracteres, as 52 colunas de `S_PRD` e os limites descritos no projeto. `S_PACBO.PACBO_PA` guarda nove dígitos; o dígito verificador é mantido no código completo gravado em `S_PRD.PRD_PA`.

As regras variam por competência. Por isso o aplicativo valida contra as tabelas efetivamente carregadas no BPA e deixa a consistência final para o BPA/SIA. Referências oficiais consultadas em 17/07/2026:

- Portal oficial do [BPA/SIA — arquivos, manual e layout](https://sia.datasus.gov.br/documentos/listar_ftp_bpa.php).
- [BPA na Wiki oficial do Ministério da Saúde](https://wiki.saude.gov.br/sia/index.php/BPA), inclusive o alerta de LGPD.
- [Downloads oficiais do SIGTAP](https://sigtap.datasus.gov.br/tabela-unificada/app/download.jsp).
- [Atributos e vigência de procedimentos no SIGTAP](https://wiki.datasus.gov.br/sigtap/index.php/Gerais).

## Limitações reais

- O PEC é um sistema externo municipal. Seletores foram implementados a partir da interface atualmente observada, mas uma mudança de tela pode produzir `ERRO_TECNICO`; o sistema não tenta contornar ou alterar o PEC.
- O OCR é opcional e conservador: renderiza páginas e gera candidatos por célula para revisão humana. Nenhuma linha de OCR é incluída automaticamente.
- A aplicação não promete aprovação no SIA/SUS e não gera diretamente `.JUL`, `.AGO` ou outra remessa definitiva.
- Não foi feita gravação definitiva de produção durante a entrega. Apenas o teste sintético com `ROLLBACK` foi executado; a primeira inclusão definitiva exige a confirmação textual na interface.
