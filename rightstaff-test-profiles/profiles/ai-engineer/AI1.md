Name: Lena Park
GitHub-username: lenapark-ai

## Resume

**Lena Park**
AI Engineer · candidate@example.com · Seattle, WA · github.com/lenapark-ai

**Experience**

**AI Engineer** — Quillstack, Seattle, WA · Mar 2024 – Present
- Built and own the retrieval-augmented support assistant (embeddings, pgvector, reranker, Claude and open-weight models via a FastAPI service); it now resolves 38% of support tickets without a human, up from 0 at launch.
- Designed the evaluation harness: 600 labeled question–answer pairs, automated groundedness and correctness checks, run on every prompt or model change in CI; caught 3 regressions before release.
- Cut median response latency from 4.1 s to 1.6 s and model cost per answer by 45% by caching embeddings, batching retrieval and routing easy questions to a smaller model.
- Fine-tuned a small classifier (LoRA on a 1B-parameter model, PyTorch + Hugging Face) for ticket routing; 92% accuracy vs 81% for the keyword rules it replaced.
- Containerized the services with Docker and deploy to AWS (ECS); added latency and failure-rate monitoring.

**Software Engineering Intern** — Pinecrest Software, Portland, OR · Jun 2023 – Sep 2023
- Built REST endpoints in Python (Flask) for an internal reporting tool; wrote unit tests that raised coverage from 40% to 75%.

**Projects**

**rag-eval-kit** (github.com/lenapark-ai/rag-eval-kit)
- Open-source toolkit for testing retrieval pipelines: test-set builder, retrieval hit-rate and answer groundedness metrics, CLI reports.

**Education**
B.S. Computer Science, Cascade Pacific University · 2023

**Skills**
Python, PyTorch, Hugging Face, LangGraph, FastAPI, Flask, Docker, AWS, PostgreSQL/pgvector, Git, RAG, LLM evaluation, fine-tuning

**Certifications**
Google Cloud Professional Machine Learning Engineer · 2025 · Credential ID GCP-PMLE-TEST-3391

## LinkedIn

Lena Park
AI Engineer at Quillstack
Seattle, Washington

Experience
- AI Engineer, Quillstack · Mar 2024 – Present · Retrieval-augmented support assistant, evaluation and model serving.
- Software Engineering Intern, Pinecrest Software · Jun 2023 – Sep 2023

Education
- Cascade Pacific University, B.S. Computer Science, 2019 – 2023

Skills: Python, PyTorch, LLMs, RAG, Docker, AWS

Licenses & certifications
- Google Cloud Professional Machine Learning Engineer · Issued 2025 · Credential ID GCP-PMLE-TEST-3391

## GitHub

```json
{
  "username": "lenapark-ai",
  "repos": [
    {"name": "rag-eval-kit", "description": "Toolkit for testing retrieval-augmented generation pipelines", "languages": ["Python"], "is_fork": false, "own_commits": 112, "archived": false, "last_push": "2026-09",
     "readme": "Build a test set from your documents, measure retrieval hit-rate@k and answer groundedness, and get a CLI report you can run in CI. Works with any retriever that returns ranked chunks."},
    {"name": "lora-ticket-router", "description": "LoRA fine-tuning example for text classification", "languages": ["Python", "Jupyter Notebook"], "is_fork": false, "own_commits": 31, "archived": false, "last_push": "2025-08",
     "readme": "Fine-tunes a 1B-parameter model with LoRA for multi-class ticket routing on a public dataset. Reports accuracy, per-class F1 and inference cost."},
    {"name": "dotfiles", "description": "Personal configuration", "languages": ["Shell"], "is_fork": false, "own_commits": 15, "archived": false, "last_push": "2026-01", "readme": ""}
  ]
}
```

## Portfolio

None provided.

## Certifications (form)

```json
[{"name": "Google Cloud Professional Machine Learning Engineer", "issuer": "Google Cloud", "date": "2025-02", "credential_id": "GCP-PMLE-TEST-3391", "verify_url": null}]
```

## References (form)

```json
[{"name": "Marcus Hale", "relationship": "manager", "contact": "reference@example.com", "statement": "Lena is the engineer behind our support assistant. She designed the evaluation harness we now use for every AI feature, and she owns the service in production."}]
```
