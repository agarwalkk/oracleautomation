QCS Oracle EBS Test Automation
==============================

This repository records natural-language Oracle EBS R12 test instructions once, then generates deterministic pytest replay scripts for browser/OAF and Java Forms flows.

Start here:

- Full project guide: docs/project-guide.md
- Copilot rules: .github/copilot-instructions.md
- User-editable recording input: instructions.txt
- Stable computer-use recorder prompt: oracle_ai_agent/cu_system_prompt.txt

Common commands:

  python -m qcs record instructions.txt --run-id rec_014 --auto-name
  python -m pytest generated_tests\rec_014 -q -s
  python -m qcs gen recordings\rec_014 rec_014 --out generated_tests\rec_014

Key rules:

- Normal replay is AI-free.
- Recording AI sees screenshots only; Java DOM stays local for coordinate mapping.
- Java Forms extraction and replay go through java-agent/ and qcs_java_agent.
- Repository access goes through qcs_repo.store.
- Do not hand-edit generated tests for lasting fixes; update the generator or repository source.
