# templates

Notes and guides for building one-click **Railway marketplace templates** that wrap upstream open-source projects.

- [RAILWAY_TEMPLATES_GUIDE.md](RAILWAY_TEMPLATES_GUIDE.md): the complete playbook, from investigating an upstream project to a published and verified template, with the platform facts, component patterns and the gotcha catalogue collected while building twenty-four templates (cognee, Multica, Fabric, LongMemory, projectmem, gortex, Observal, WeKnora, EverOS, Notesnook, Pipecat, OpenPencil, Persistent mise Workspace, Apache HertzBeat, Laminar, OpenKnowledge, tlbx, codeg, Orca, DSH + LongMemory, Mirage Daemon, Yao Agents, HolyClaude Workstation, Octop).

Template repositories built with it:

| Template | Deploy | Repo |
|---|---|---|
| Cognee AI Memory Platform with MCP | https://railway.com/deploy/cognee-ai-memory-p-1 | [cognee_railway_template](https://github.com/RockinPaul/cognee_railway_template) |
| Multica | https://railway.com/deploy/multica | [multica_railway_template](https://github.com/RockinPaul/multica_railway_template) |
| LongMemory | https://railway.com/deploy/longmemory | [longmemory_railway_template](https://github.com/RockinPaul/longmemory_railway_template) |
| Fabric | https://railway.com/deploy/fabric | [fabric_railway_template](https://github.com/RockinPaul/fabric_railway_template) |
| projectmem | https://railway.com/deploy/projectmem | [projectmem_railway_template](https://github.com/RockinPaul/projectmem_railway_template) |
| gortex | https://railway.com/deploy/gortex | [gortex_railway_template](https://github.com/RockinPaul/gortex_railway_template) |
| Observal | https://railway.com/deploy/observal | [observal_railway_template](https://github.com/RockinPaul/observal_railway_template) |
| WeKnora | https://railway.com/deploy/weknora | [weknora_railway_template](https://github.com/RockinPaul/weknora_railway_template) |
| EverOS | https://railway.com/deploy/everos | [everos_railway_template](https://github.com/RockinPaul/everos_railway_template) |
| Notesnook Sync Server | https://railway.com/deploy/notesnook-sync-server | [notesnook_railway_template](https://github.com/RockinPaul/notesnook_railway_template) |
| Pipecat | https://railway.com/deploy/pipecat | [pipecat-railway-template](https://github.com/RockinPaul/pipecat-railway-template) |
| OpenPencil | https://railway.com/deploy/openpencil | [openpencil-railway-template](https://github.com/RockinPaul/openpencil-railway-template) |
| Persistent mise Workspace | https://railway.com/deploy/persistent-mise-workspace | [mise-railway-template](https://github.com/RockinPaul/mise-railway-template) |
| Apache HertzBeat | https://railway.com/deploy/apache-hertzbeat | [hertzbeat_railway_template](https://github.com/RockinPaul/hertzbeat_railway_template) |
| Laminar | https://railway.com/deploy/laminar | [lmnr_railway_template](https://github.com/RockinPaul/lmnr_railway_template) |
| OpenKnowledge | https://railway.com/deploy/openknowledge | [openknowledge_railway_template](https://github.com/RockinPaul/openknowledge_railway_template) |
| tlbx | https://railway.com/deploy/tlbx | [tlbx_railway_template](https://github.com/RockinPaul/tlbx_railway_template) |
| codeg | https://railway.com/deploy/codeg | [codeg_railway_template](https://github.com/RockinPaul/codeg_railway_template) |
| Orca | https://railway.com/deploy/orca | [orca_railway_template](https://github.com/RockinPaul/orca_railway_template) |
| DSH + LongMemory | https://railway.com/deploy/dsh-longmemory | [dsh_railway_template](https://github.com/RockinPaul/dsh_railway_template) |
| Mirage Daemon | https://railway.com/deploy/mirage-daemon | [mirage_railway_template](https://github.com/RockinPaul/mirage_railway_template) |
| Yao Agents | https://railway.com/deploy/yao-agents | [yao_railway_template](https://github.com/RockinPaul/yao_railway_template) |
| HolyClaude Workstation | https://railway.com/deploy/holyclaude-workstation | [holyclaude_railway_template](https://github.com/RockinPaul/holyclaude_railway_template) |
| Octop | https://railway.com/deploy/octop | [octop_railway_template](https://github.com/RockinPaul/octop_railway_template) |

## Weekly upstream update assessment

[`scripts/assess_template_updates.py`](scripts/assess_template_updates.py) runs every Friday via [GitHub Actions](.github/workflows/weekly-template-assessment.yml), reads the upstream version each template repo pins, compares it with the upstream project's releases and default branch, and writes a report to [`reports/`](reports/) (newest: [`reports/latest.md`](reports/latest.md)). The list of templates and the pin locations live in [`templates.yaml`](templates.yaml); scoring rules and local usage are in [`scripts/README.md`](scripts/README.md).

The September 9 additions include component patterns in guide sections 5.11–5.13 and case-file entries in section 8. Section 3.9.1 records the verified CLI/API template-editing workflow; marketplace metadata and defaults are not limited to manual dashboard editing.
