Name: Noah Kim
GitHub-username: noahk-builds

## Resume

**Noah Kim**
New-grad AI/ML engineer · candidate@example.com · San Jose, CA · github.com/noahk-builds

**Education**
B.S. Computer Science (AI track), Bayview State University · May 2026 · GPA 3.6
Coursework: Machine Learning, Deep Learning, NLP, Distributed Systems, Databases

**Projects**

**ReviewBot — LLM code-review assistant** · Jan 2026 – May 2026 · (github.com/noahk-builds/reviewbot)
- GitHub app that comments on pull requests using an LLM with retrieval over the repo's style guide and past reviews; served with FastAPI in Docker.
- Built an evaluation set of 300 real pull-request comments; precision of useful comments went from 41% to 68% after adding retrieval and a self-check step.
- Used by 4 student project teams (about 25 developers) during the spring term.

**Fine-tuned legal-clause classifier** · Sep 2025 – Dec 2025 · (github.com/noahk-builds/clause-classifier)
- LoRA fine-tune of a 1B-parameter model with PyTorch and Hugging Face on a public contract dataset; macro-F1 0.81 vs 0.72 for a prompted baseline, at one-tenth the inference cost.

**Course-notes RAG assistant** · Mar 2025 – May 2025 · (github.com/noahk-builds/notes-rag)
- Embeddings and vector search over 900 pages of lecture notes; measured retrieval hit-rate@5 (0.84) and documented failure cases on tables and formulas.

**Bay Area AI Hackathon 2025** — 1st place, "Best use of retrieval" · Nov 2025
- Team of 4; built a voice assistant that answers questions about city permits from public documents.

**Skills**
Python, PyTorch, Hugging Face, FastAPI, Docker, Git, LangGraph, PostgreSQL, RAG, LLM evaluation, fine-tuning

## LinkedIn

Noah Kim
CS (AI) graduate, Bayview State University
San Jose, California

Education
- Bayview State University, B.S. Computer Science, 2022 – 2026

Projects
- ReviewBot — LLM code-review assistant (2026)
- Fine-tuned legal-clause classifier (2025)

Honors & awards
- 1st place, "Best use of retrieval", Bay Area AI Hackathon 2025

Skills: Python, PyTorch, LLMs, RAG, FastAPI

## GitHub

```json
{
  "username": "noahk-builds",
  "repos": [
    {"name": "reviewbot", "description": "LLM code-review assistant with retrieval over style guides", "languages": ["Python", "Dockerfile"], "is_fork": false, "own_commits": 141, "archived": false, "last_push": "2026-05",
     "readme": "GitHub app that reviews pull requests. Retrieves relevant style-guide sections and past review comments, drafts comments, then runs a self-check. Includes an evaluation set of 300 labeled comments and a script that reports precision of useful comments."},
    {"name": "clause-classifier", "description": "LoRA fine-tune for contract clause classification", "languages": ["Python", "Jupyter Notebook"], "is_fork": false, "own_commits": 38, "archived": false, "last_push": "2025-12",
     "readme": "Fine-tunes a 1B-parameter model with LoRA. Compares macro-F1 and cost with a prompted baseline."},
    {"name": "notes-rag", "description": "RAG over course notes with retrieval metrics", "languages": ["Python"], "is_fork": false, "own_commits": 27, "archived": false, "last_push": "2025-05",
     "readme": "Chunking, embeddings and vector search over lecture notes. Reports hit-rate@5 and lists failure cases."},
    {"name": "permit-voice-assistant", "description": "Hackathon winner: voice Q&A over city permit documents", "languages": ["Python", "TypeScript"], "is_fork": false, "own_commits": 22, "archived": false, "last_push": "2025-11",
     "readme": "Bay Area AI Hackathon 2025, team of 4. Speech-to-text, retrieval over permit PDFs, spoken answers with citations."}
  ]
}
```

## Portfolio

None provided.

## Certifications (form)

None provided.

## References (form)

```json
[{"name": "Dr. Sam Rivera", "relationship": "supervisor", "contact": "reference@example.com", "statement": "I supervised Noah's senior project, ReviewBot. He built the evaluation set himself and used it to decide every design change."}]
```
