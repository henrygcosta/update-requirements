# update_requirements

Automatiza a atualização de um conjunto fixo de dependências no `requirements.txt`
de vários repositórios Git já clonados localmente, validando conflitos de
dependência com ferramentas reais do ecossistema pip antes de commitar.

## O que o script faz, por repositório

1. Verifica se a working tree está limpa (se não estiver, o repositório é
   **pulado** — nunca descarta trabalho não commitado do usuário).
2. `git checkout <branch>` + `git pull --ff-only`.
3. Atualiza apenas as dependências listadas em `target_dependencies` no
   `requirements.txt`, preservando comentários, linhas em branco, ordem e
   formatação de todo o resto do arquivo. Dependências ausentes são
   adicionadas ao final. Se uma dependência-alvo existir no arquivo mas em um
   formato que a ferramenta não edita com segurança (pin por hash, continuação
   de linha, espaçamento incomum no specifier), a linha **não é tocada nem
   duplicada** — ela é reportada em `unparsed_deps` no relatório para revisão
   manual.
4. Se não houve alteração no conteúdo, registra "sem alterações" e segue para
   o próximo repositório.
5. Se houve alteração, cria um **ambiente virtual efêmero**, instala o
   `requirements.txt` atualizado nele (usando o resolver real do `pip`), roda
   `pip check` e `pipdeptree --warn fail`. Nenhuma regra de compatibilidade é
   codificada manualmente — os conflitos vêm diretamente dessas ferramentas.
6. Sem conflitos → `git add` + `git commit -m "update libs"` automaticamente.
7. Com conflitos → mostra o resumo (biblioteca, motivo, saída das ferramentas)
   e pergunta `Deseja realizar o commit mesmo assim? (y/N)`. Só commita após
   confirmação explícita; se recusado, a alteração é revertida em disco.
8. Push é opcional e desligado por padrão (veja `push` no config / `--push`).
   Se o commit for feito mas o push falhar, o arquivo **não é revertido**
   (o commit já está no histórico local) — o repositório fica com o status
   `committed_push_failed`, ainda contado em "commits realizados", para você
   resolver o push manualmente.

Ao final, imprime um relatório consolidado no console e grava uma cópia em
`reports/run_<timestamp>.{md,json}`. Logs detalhados de cada execução ficam em
`logs/run_<timestamp>.log`.

## Pré-requisitos

- Python 3.9+ e `git` disponíveis no `PATH`.
- Os repositórios-alvo já clonados localmente, com acesso ao remoto
  configurado (necessário para `git pull` e, se habilitado, `git push`).

## Instalação

```bash
git clone <url-deste-repositorio>
cd update-requirements
python -m venv .venv
# Windows:
.venv\Scriptsctivate
# Linux/macOS:
# source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

Copie `config.example.yaml` para `config.yaml` (este arquivo é específico de cada
pessoa e está no `.gitignore` — não é versionado) e ajuste:

- `repositories`: caminhos locais dos repositórios já clonados (cada um pode
  sobrescrever `requirements_path` e/ou definir um `name` amigável).
- `target_dependencies`: lista `nome==versao` das dependências a atualizar.
- `branch`, `commit_message`, `push`, `requirements_path`, `log_level`,
  `report_dir`, `log_dir`.

## Uso

```bash
# Primeira execução: sempre valide em dry-run antes de rodar de verdade.
python -m update_requirements --config config.yaml --dry-run

# Execução real (pede confirmação interativa quando há conflito):
python -m update_requirements --config config.yaml

# Execução real com push automático após cada commit:
python -m update_requirements --config config.yaml --push

# Modo não-interativo (útil em CI/batch): nunca bloqueia em prompt. Repositórios
# com conflito ficam com status "awaiting_confirmation" no relatório e a
# alteração é revertida, para revisão manual posterior.
python -m update_requirements --config config.yaml --non-interactive
```

Outras flags: `--no-push` (força desligar push mesmo se `push: true` no
config), `-v/--verbose` (equivale a `--log-level DEBUG`), `--log-level`.

## Avisos importantes

- **`--non-interactive` não é "sem risco"**: ele nunca commita algo com
  conflito sem revisão humana — apenas evita bloquear em `input()`. Trate o
  bucket "aguardando confirmação" do relatório final como pendência real.
- A validação de conflitos usa o **PyPI público**. Se algum repositório
  depende de pacotes privados/internos não publicados publicamente, a
  instalação no venv efêmero pode falhar por esse motivo (não por conflito de
  versão real) — a saída completa do pip fica no relatório para você
  diferenciar os dois casos antes de confirmar o commit.
- O script nunca mexe em repositórios com alterações não commitadas
  pré-existentes (`dirty_skipped`) e nunca força merge em `git pull` (usa
  `--ff-only`) — se a branch divergiu, o repositório vai para "erro
  inesperado" para você resolver manualmente.
- É idempotente: rodar de novo sobre repositórios já atualizados não gera
  diffs nem commits novos.

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```
