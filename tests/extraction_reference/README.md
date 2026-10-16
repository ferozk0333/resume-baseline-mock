# Extraction reference (not in git)

`resume.json` and `linkedin_pdf.json` hold a saved Claude (Haiku 4.5) extraction of a real resume and LinkedIn
PDF, with the exact normalized source text. They contain personal data, so this folder is gitignored; only this
README is committed. `scripts/compare_extraction.py --provider <name>` compares any provider against them.
