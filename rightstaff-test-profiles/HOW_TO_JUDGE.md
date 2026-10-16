# How to run the judge checks (manual, about 14 chats)

## What you need
- The 7 files in `judge_packs/`. Each one is complete: prompt, role, and five candidates. Paste the whole file as one message.
- Claude and Gemini chatbots.
- `judge_results.csv` to record answers (or paste the raw answers back to me).

## Rules for every chat
1. **New chat each time.** Use an incognito or temporary chat if the app offers one. Otherwise turn off memory and saved chat history first, so one answer cannot influence the next.
2. Paste the whole pack, unchanged. Don't add your own comments.
3. Don't ask follow-up questions or say "are you sure?". The first answer is the result.
4. Copy the `RANKING`, `GAPS` and `DECIDING FACTOR` lines into the sheet. Keep the `REASONS` too; they explain disagreements.

## The 14 chats

| # | Pack | Claude | Gemini |
|---|---|---|---|
| 1 | `01_data-scientist_forward` | ☐ | ☐ |
| 2 | `02_data-scientist_reversed` | ☐ | ☐ |
| 3 | `03_data-scientist_lengthcheck` | ☐ | ☐ |
| 4 | `04_ai-engineer_forward` | ☐ | ☐ |
| 5 | `05_ai-engineer_reversed` | ☐ | ☐ |
| 6 | `06_product-manager_forward` | ☐ | ☐ |
| 7 | `07_product-manager_reversed` | ☐ | ☐ |

"Reversed" has the same candidates and labels in reverse order. It checks whether a judge favors whoever comes first.

The length check is the Data Scientist set with one candidate (B) padded with fluff and no new facts. If B moves up compared with chat 1, that judge is swayed by length, and we note it.

## What happens with the answers
Send them to me. For each role I will:
1. Turn each ranking into its 10 pairs (A vs B, A vs C, …).
2. Mark a pair **trusted** when all four answers (2 judges × 2 orders) agree on the winner. If a judge changes its winner when the order is reversed, that pair counts as a tie for that judge.
3. Compare our system's order with the trusted pairs: "our system gets X of Y trusted pairs right".
4. For each trusted pair we get wrong, compare the judges' reasons with our subcategory scores to find the formula or weight responsible.

Do **not** open `DESIGN.md` before judging is finished if you want to stay unbiased when reading the answers. It holds the label key and the order we expected when writing the profiles.
