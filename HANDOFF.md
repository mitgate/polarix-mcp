# HANDOFF — Polarix MCP

Estado do projeto para quem pegar o próximo passo (humano ou agente).

## Estado atual (2026-10-02)

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
* **VM** (`vm_*`): `polarix/vm/backends.py` — libvirt (`virsh`), VirtualBox
  (`VBoxManage`), Android (`adb`/`emulator`) atrás de um `runner` injetável.
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

## Pendências e riscos (em ordem)

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
