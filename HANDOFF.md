# HANDOFF — Polarix MCP

Estado do projeto para quem pegar o próximo passo (humano ou agente).

## Estado atual (2026-10-03)

* **Nome**: o produto é **Polarix** (o nome Polaris já existia no PyPI). Pacote Python
  `polarix/`, servidor FastMCP "Polarix", bloco de telemetria `_polarix`, variáveis
  `POLARIX_*` (as `POLARIS_*` antigas continuam sendo lidas como fallback). O remoto Git
  ainda se chama `mitgate/polaris-mcp` — renomear no GitHub é opcional.
* **Browser** (`browser_*`): inalterado em comportamento; só o rename.
* **Desktop** (`desktop_*`): Map First para janelas nativas. Camadas:
  * `polarix/desktop/model.py` — `Control`, locators, `control_index`, diff de árvore.
  * `polarix/desktop/driver.py` — contrato `DesktopDriver` + `get_driver()`
    (`remote` | `auto`/`pywinauto` | `fake`).
  * `polarix/desktop/pywinauto_driver.py` — implementação Windows (UIA/win32).
  * `polarix/desktop/remote_driver.py` + `agent.py` — host → agente no guest, JSON/HTTP
    só com stdlib; o agente importa sem Playwright/MCP SDK.
  * `polarix/desktop/steps.py` — `run_desktop_steps()`: steps, `assert`, `wait_idle`,
    `click_image`, `click_vision`, e **auto-recuperação de locators** (`resolve_locator`).
  * `polarix/desktop/vision.py` — template matching (OpenCV) e localizar/verificar por
    modelo multimodal (`vision_json`).
  * `polarix/desktop/scenarios.py` — cenários (JSON/YAML), suíte, KPIs, relatório HTML,
    `scenario_from_macro`.
  * `polarix/desktop/recorder.py` + `macros.py` — gravação (pynput) e macros nomeadas.
  * `polarix/desktop/fake_driver.py` — app simulado (editor + "Salvar como") que também
    **renderiza screenshot** a partir dos rects, para testar matching de imagem.
* **Métricas** (`metrics_*`): `polarix/metrics/` — `ledger.py` (SQLite alimentado por
  `telemetry._wrap`, extrai runs/steps/maps/scenarios de qualquer resposta), `indicators.py`
  (19 indicadores com direção "melhor", janela atual × anterior, deriva do mapa, flakiness,
  health score), `charts.py` (SVG sem dependências + dashboard HTML).
* **VM** (`vm_*`): `polarix/vm/backends.py` — libvirt (`virsh`), VirtualBox
  (`VBoxManage`), Android (`adb`/`emulator`) atrás de um `runner` injetável.
* **Ambientes** (`environment_*`): `polarix/desktop/environments.py` — políticas de ciclo de vida,
  decisão (`needs_decision`), estado em JSON, matriz `${var}` nos cenários.
* **Testes**: `python3.11 -m pytest tests` — modelo, steps, asserts, healing, visão
  (com LLM simulado), cenários/suíte/relatório, gravador, macros, agente ↔ driver remoto
  no loopback, backends de VM com runner roteirizado, camada de tools. Todos passam no
  Fedora com o driver `fake`.
* **Scripts**: `scripts/agent_windows.ps1` sobe o agente dentro do guest.

## O que mudou nesta entrega

1. Rename Polaris → Polarix em todo o repositório.
2. Camada desktop completa (conhecimento, execução, verificação, macros) + agente remoto.
3. Camada de VM (ciclo de vida, snapshots, screenshot e teclas via hipervisor).
4. **Testes como produto**: `assert` em 12 tipos, cenários com setup/teardown e restore
   de snapshot, suíte com KPIs (pass rate, assertion rate, duração, mais lento, locators
   recuperados), relatório HTML com screenshot da falha, macro → cenário.
5. **Canvas**: `desktop_find_image`/`click_image`/`wait_for image` (OpenCV) e
   `desktop_vision_locate`/`desktop_vision_verify`/`click_vision`/`assert vision`
   (modelo multimodal).
6. **Locator healing**: hint gravado (`_recorded`) e título difuso; `healed_locator` no
   resultado e contagem na suíte.
7. `polarix/__init__.py` deixou de importar as tools; `browser_python_mcp.py` importa
   `polarix.tools` explicitamente, para o guest rodar o agente sem Playwright.
8. `llm.py`: `generate_desktop_steps()`; prompts viraram funções testáveis.
9. `telemetry._polarix()` aceita `desktop=`.
10. README: seções de desktop/VM, cenários e KPIs, healing, fallbacks de canvas.
16. **Ambientes e política de ciclo de vida** (1.6.0): `polarix/desktop/environments.py` +
    `polarix/tools/environments.py` (6 tools `environment_*`). Ambiente = alvo + passos
    `install`/`uninstall`/`reset` + `variables`; três camadas (infra = VM/snapshot, sistema =
    o que está instalado, estado = dados do app); políticas `fresh` (snapshot + install),
    `reinstall` (uninstall + install), `reset` (limpa dados), `keep` (nada) e `keep_after`
    (manter de pé ou desinstalar + reverter ao final). **Sem política e sem default a tool
    devolve `needs_decision` com opções e recomendação — a IA pergunta, nunca escolhe.**
    Estado (preparado? desde quando? quantas rodadas) em `POLARIX_ENV_STATE`. Cenários
    ganharam `matrix: {var: [...]}` com substituição `${var}`/`${var.field}` (a matriz pode
    apontar para uma lista das variáveis do ambiente, ex. `${browsers}`) — é assim que o
    mesmo teste roda em vários navegadores instalados na mesma VM. Instruções do servidor
    têm a seção ENVIRONMENTS com a regra de perguntar.
15. **Driver Android** (1.5.0): `polarix/desktop/android_driver.py` — contrato `DesktopDriver`
    sobre `adb` + `uiautomator dump`, sem agente no aparelho (roda onde o adb roda: emulador
    local, `adb connect`, device farm). Janela = activity em primeiro plano; `title` =
    content-desc ou text, `value` = text, `auto_id` = resource-id; `menu_select` não existe;
    `shell` roda NO APARELHO. Driver `android` em `get_driver`/alvos (`serial`). Só testado
    com adb roteirizado. **iPhone**: não há caminho em Linux — exige macOS (EC2 Mac ou device
    farm) com Appium/XCUITest; o driver `appium` (W3C WebDriver) seria o próximo passo e
    cobriria Android e iOS com uma implementação.
13. **Alvos nomeados** (1.4.0): `polarix/desktop/targets.py` — `targets.json`/`POLARIX_TARGETS`
    (nome → driver, agent_url, token, os, vm); `target` em todas as tools desktop, `target`/
    `targets` (matriz) nos cenários, `POLARIX_TARGET` como padrão, `desktop_targets`.
14. **`desktop_map_app`** (1.4.0): crawler BFS da aplicação (menus, botões "…", abas), mapeia
    e fecha cada diálogo (Cancel/Close/Escape, nunca OK), reabre pelo caminho registrado para
    explorar em profundidade, pula sair/destrutivo, orçamento de janelas e tempo; `feature_index`
    + `coverage`; entra no ledger como mapa (drift da aplicação inteira).
12. **Passo `shell` e `desktop_run_command`** (1.3.0): comando no alvo (no guest, via
    agente) com código de saída esperado — instalar/desinstalar (`winget`), fixtures,
    limpeza — para setup/teardown de cenários. `run_command` entrou no contrato
    `DesktopDriver` (fake, pywinauto, remoto) e no `ALLOWED_METHODS` do agente.
11. **Métricas** (1.2.0): ledger automático em `_wrap`, indicadores por grupo do mapa
    (map, locators, execution, tests, speed) com tendência e health score, `metrics_*`
    tools, dashboard HTML. Tools `metrics_*` não se registram; `POLARIX_METRICS=off` desliga.

## Pendências e riscos (em ordem)

0. **Ambientes só rodaram no driver `fake`**: `install`/`uninstall` via `shell` no guest real
   (winget/apt/adb install) nunca foram exercitados; `fresh` depende de `target.vm.snapshot`
   e de libvirt/VBox presentes. Falta `environment_run` com `restore_vm` por cenário (hoje a
   restauração é só a do ambiente) e um "lock" para dois clientes não prepararem o mesmo
   ambiente ao mesmo tempo.

1. **O driver pywinauto nunca rodou de verdade** — foi escrito no Fedora. Primeiro
   teste dentro da VM Windows, nesta ordem:
   1. `scripts\agent_windows.ps1 -Token x` e `vm_agent_check` do host.
   2. `desktop_list_windows()`; `desktop_map_window('{"title_re": ".*Bloco de notas.*"}')`.
   3. O fluxo do README (menu → wait_for window → set_text → click) no Bloco de notas.
   4. Só então a aplicação alvo (CAD): ver quanto da interface a árvore UIA expõe
      (`win32` às vezes enxerga mais em apps Delphi). Pontos frágeis prováveis:
      `menu_items(expand=True)` no backend `uia`, `set_text` em controles que não são
      Edit, `launch()` quando o app abre splash antes da janela principal,
      `capture_as_image()` com janelas parcialmente fora da tela.
2. **Visão**: `vision_json` nunca falou com um modelo real (testes usam dublê). Validar
   o parse do JSON de resposta com Claude e com GPT, e o custo por chamada em suítes
   grandes — preferir `find_image` sempre que houver crop.
3. **Template matching é sensível a escala/DPI**: a VM precisa manter resolução e
   escala fixas entre gravação e replay. Multi-escala não foi implementado.
4. **Gravador** usa `control_from_point` do driver; no remoto cada clique vira uma
   chamada HTTP. Não testado com teclado real.
5. **Driver `fake` é por chamada** no `get_driver()`: para estado persistente no Linux,
   suba o agente com `--driver fake` e use `remote`.
6. **Hipervisor nesta máquina**: KVM habilitado mas libvirt/virsh não instalado; `adb`
   existe. `vm_backends()` mostra o que há. Sugestão:
   `sudo dnf install libvirt qemu-kvm virt-install virt-viewer`.
7. **Outros guests**: Linux (AT-SPI), macOS (Accessibility) e Android (uiautomator2)
   implementariam o mesmo contrato `DesktopDriver` no mesmo agente.
8. **Segurança do agente**: token em header, HTTP sem TLS — só para a rede interna da VM.
9. **Compatibilidade**: telemetria mudou de `_polaris` para `_polarix`.
12. **Android na AWS**: emulador precisa de KVM (instâncias `.metal`); alternativas: Genymotion
    Cloud (AMI), aparelho real via `adb connect`, ou device farm com adb exposto. O crawler
    (`desktop_map_app`) ainda não entende Android (sem menus; openers = clickables; voltar =
    KEYCODE_BACK).
11. **Crawler**: só testado no app simulado. Em apps reais, menus `uia` com submenus em
    cascata, diálogos modais que bloqueiam o foco e janelas com títulos iguais (chave é o
    título) são os pontos frágeis; `action_wait` curto demais perde diálogos lentos.
10. **Métricas**: limiares (2 pp em taxas, 10 % em durações, mínimo 5 amostras) e pesos do
    health score são chutes razoáveis — calibrar com dados reais. O ledger cresce sem
    poda; `metrics_export` + apagar o SQLite é a limpeza por enquanto. Drift usa só os
    identificadores (não posição/texto).

## Como rodar

```bash
./start.sh                                   # servidor MCP em http://127.0.0.1:8016/mcp
python3.11 -m pytest tests -q                # suíte (driver fake)
POLARIX_DESKTOP_DRIVER=fake ./start.sh       # desktop_* contra o app simulado
python3.11 -m polarix.desktop.agent --driver fake --port 8020   # agente simulado persistente
```

Contexto de produto: a camada desktop existe para automatizar testes funcionais de
aplicações Windows nativas (CAD, orçamentação) rodando numa VM controlada pelo Polarix —
gravar roteiros manuais como macros, transformar em cenários com asserções e deixar um
agente executar, validar e medir.
