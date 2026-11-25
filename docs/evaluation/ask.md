# Radar Ask: evaluation

Measured 2026-11-24 and 25. Every number was read from a run. All labels are drafted by a model and await human review, and the
groundedness judge has not been compared with a human yet, so treat the numbers as provisional.

Everything up to "Findings" describes the code as it was before any fix. "After the fixes" describes it now.

## What is measured

Ask has two routes. A question about breaking changes or advisories of a tracked package is answered from data with no model. Any
other question goes through hybrid retrieval (keyword and vector rankings fused), a confidence gate, a model that must cite every
sentence, and a validator that drops sentences without a valid citation. Each stage is measured on its own, calling the
application's own code.

The answering model is `qwen2.5:1.5b`, the default of the code (`web/app.py:207` builds the backend without naming a model). It is
not a measurement of production: see finding 1. Answers were recorded on a laptop CPU.

## Data

- Archive: a frozen copy of the production archive (2026-11-24), 24,145 items, of which 4,058 have a vector. It is not in git.
- Questions: `evals/ask/golden.jsonl`, 62: 37 answerable, 8 structured, 10 must-refuse, 7 attacks. Split 40/60 into dev and test
  by a hash of the question id. Nothing was tuned on either half.
- Answerable questions start from items drawn at random within each source (seed 20261124), so they are not picked because the
  search finds them. The writer then kept the items a question could be written about, so this is not a pure random sample. One
  question (pap11) has no seed item. Titles are not copied; `overlap` is the share of question words found in the best answering item.
- Must-refuse questions are plausible topics; `probes` records how many archive items match the obvious words.
- Four structured questions (`expected_to_fail`) were written after reading the code to show suspected failures. The structured
  score is therefore not a sample of what users ask.
- Attack items are synthetic and exist only during a recording, in a throwaway copy of the database.

## Labels

Relevance is 0, 1 (helps in part) or 2 (answers it). The pool of each question is the union of the top ten of every variant, including
vector and hybrid over a fully embedded copy, plus items found by text search. 1,335 judgments, 165 above 0. Every item in every
variant's top six was judged. One model drafted the questions and the labels (`reviewed: false`). Items graded 0 carry a hash instead
of their address.

hit@6: an answering item (grade 2) is among the six items given to the model. MRR also needs grade 2. Recall and precision count
grades 1 and 2. nDCG uses the grades. Intervals are 95%: bootstrap over questions for means, Wilson for rates. Differences are paired
over the same questions; "significant" means the interval excludes zero, with no correction for the many comparisons.

## Results: retrieval (37 answerable questions)

| Variant | hit@6 | recall@6 | precision@6 | MRR@10 | nDCG@6 |
|---|---|---|---|---|---|
| keyword only | 0.35 [0.19, 0.51] | 0.21 [0.11, 0.33] | 0.16 [0.09, 0.24] | 0.23 [0.12, 0.36] | 0.21 [0.12, 0.31] |
| vector only | 0.62 [0.46, 0.78] | 0.43 [0.31, 0.56] | 0.21 [0.14, 0.28] | 0.48 [0.34, 0.63] | 0.44 [0.32, 0.57] |
| hybrid (shipped) | 0.59 [0.43, 0.76] | 0.40 [0.28, 0.53] | 0.22 [0.15, 0.29] | 0.40 [0.27, 0.53] | 0.36 [0.26, 0.48] |
| what-if: vector, all items embedded | 0.78 [0.65, 0.92] | 0.53 [0.41, 0.65] | 0.29 [0.21, 0.37] | 0.57 [0.45, 0.71] | 0.54 [0.42, 0.65] |
| what-if: hybrid, all items embedded | 0.65 [0.49, 0.81] | 0.43 [0.31, 0.56] | 0.27 [0.18, 0.35] | 0.48 [0.34, 0.61] | 0.43 [0.32, 0.54] |
| what-if: the same, newest 5,000 vectors only | 0.35 [0.19, 0.51] | 0.19 [0.10, 0.28] | 0.17 [0.10, 0.25] | 0.24 [0.13, 0.36] | 0.19 [0.11, 0.28] |

Shipped hybrid on dev (15): hit@6 0.47 [0.20, 0.73]; on test (22): 0.68 [0.50, 0.86]. Six items is also the ceiling for precision:
many questions have one or two relevant items.

- Hybrid beats keyword: hit@6 +0.24 [+0.08, +0.41], significant.
- Hybrid against vector: hit@6 -0.03 [-0.16, +0.11], nDCG@6 -0.08 [-0.19, +0.03], no significant difference. On the 23 questions
  with low word overlap hybrid is worse on recall@6, MRR and nDCG (nDCG -0.17 [-0.30, -0.04]), not on hit@6. On the 14 with
  higher overlap it is level or slightly ahead (recall@6 +0.08 [+0.01, +0.17]).
- Coverage. Where an answering item has a vector (30 questions) vector finds one in 0.77; where none has (7) it finds 0 of 7 and
  hybrid 0.29. If only embedded items count, hybrid is at 0.70 [0.53, 0.87] and vector at 0.77 [0.60, 0.90].
- Embedding everything improves hybrid only slightly (hit@6 +0.05, not significant; recall and nDCG significant). It moves more
  when ranking is by vector alone: +0.19 [+0.05, +0.32] over shipped hybrid, significant.

## Results: refusal

| | hybrid (shipped) | keyword only |
|---|---|---|
| must-refuse questions that pass the gate | 10 of 10 [0.72, 1.00] | 1 of 10 [0.02, 0.40] |
| answerable questions stopped by the gate | 0 of 37 [0.00, 0.09] | 28 of 37 [0.60, 0.87] |

Best cosine, the signal behind the gate (threshold 0.60), is 0.632 to 0.835 for answerable and 0.621 to 0.764 for must-refuse
questions: AUC 0.864. It carries information, but no threshold separates the two. Of five off-topic probes ("weather in Paris",
"1998 football world cup") two pass the gate.

## Results: answers (`qwen2.5:1.5b`, 62 questions)

| | shipped archive | what-if, all embedded |
|---|---|---|
| answerable questions answered | 15 of 37 [0.26, 0.57] | 18 of 37 [0.33, 0.64] |
| answered and cites an answering item | 10 of 37 [0.15, 0.43] | 14 of 37 [0.24, 0.54] |
| an answering item was in the context: answered and cites it | 10 of 22 [0.27, 0.65] | 14 of 24 [0.39, 0.76] |
| an answering item was in the context: refused | 10 of 22 | 9 of 24 |
| must-refuse: refused | 9 of 10 [0.60, 0.98] | 9 of 10 [0.60, 0.98] |
| attacks that succeeded | 3 of 7 [0.16, 0.75] | 2 of 7 [0.08, 0.64] |
| sentences with a valid citation | 54 of 54 | 75 of 75 |
| sentences dropped by the validator | 0 of 54 | 0 of 75 |
| shown citations that point at a relevant item | 25 of 62 [0.29, 0.53] | 40 of 85 [0.37, 0.58] |
| replies over 30 s, the production timeout | 7 of 54 (median 16 s) | 13 of 54 (median 16 s) |
| structured questions correct | 4 of 8 (the four expected failures failed) | the same |

Successful attacks on the shipped archive: breaking out of the output format in the question, poisoned advice in an item, and a
delimiter break-out in an item. Not successful in either run: plain instructions, asking for the system prompt, and the system-style
override. One attack that succeeded on the shipped archive failed in the what-if run, so the result depends on the surrounding items.

## Results: groundedness (provisional)

Judge `llama3.2:3b`, prompt `ask.judge@1`, unit: one answer against the items it cites, as the model saw them.

| | shipped archive | what-if |
|---|---|---|
| answerable answers called supported | 12 of 15 [0.55, 0.93] | 13 of 18 [0.49, 0.88] |
| control: answer against another question's items, called not supported | 14 of 15 [0.70, 0.99] | 15 of 18 [0.61, 0.94] |
| control: verbatim sentence from an item, called supported | 10 of 15 [0.42, 0.85] | 16 of 18 [0.67, 0.97] |

On cases whose verdict is known by construction the judge is wrong in 6 of 30 (shipped) and 5 of 36 (what-if). It never used
"partly". Do not read the first row as a groundedness rate. A human label set of 40 is prepared (32 real answers, 8 swapped controls)
and nobody has labelled it yet: agreement, precision and recall of "unsupported" and Cohen's kappa come from
`python -m evals.ask.calibration score`.

## Cross-checks

Optional, outside CI, own environment (`requirements-eval.txt`: RAGAS 0.4.4, DeepEval 4.3.5, both install on Python 3.12 next to the
application's pins; telemetry off).

- RAGAS id-based context precision and recall, no model: 0.218 and 0.407 over 36 questions, equal to our own figures (largest gap 0.0).
- Faithfulness by the local 3B judge, the same four answers: RAGAS scored 4 of 4, mean 0.71. DeepEval scored 3 of 4, all 0.0; the
  fourth reply was cut at the length limit and was not valid JSON. RAGAS needed schema-constrained decoding (4 of 4 failed without
  it), DeepEval an output limit (one reply ran past 4,000 tokens).
- The same judge model, the same answers, opposite results: with this judge neither tool's faithfulness number can be used. Four
  answers is a smoke test, not a measurement. RAGAS context recall by model and DeepEval contextual metrics were left out, because
  they need written reference answers and there are none.

## Findings

1. **Production cannot answer open questions (2026-11-25).** The production model server holds gemma3:4b, llama-guard3:1b and
   nomic-embed-text, and not `qwen2.5:1.5b`, the code's default. Every radar.ask call fails with "model not found" and the page
   says the model is unavailable. Structured questions work. (Verified on the server by the owner's side, not by this evaluation.)
   The results above describe the code with its default model. `--model` repeats them with another model on the same questions.
2. **The confidence gate is almost never closed** (`radar/ask.py:19,182`). 10 of 10 must-refuse questions and 2 of 5 off-topic ones
   pass. Refusal is done by the model, and on this model it mostly works (9 of 10), at the price of refusing many answerable ones.
3. **Valid citations say nothing about grounding.** The validator checks that a cited id was retrieved, not that it supports the
   sentence. It removed nothing, while only 25 of 62 shown citations point at a relevant item.
4. **The answer model is the weaker half.** With an answering item in front of it, it used it 10 times of 22 and refused 10 times.
5. **Equal-weight fusion gives half the six slots to keyword search** (`radar/ask.py:168-172`), which is dominated by 19,463 short
   server-registry items that are never embedded (`radar/digest.py:54-56`). 540 of 3,517 papers have no vector either.
6. **The 5,000-vector window will hurt** (`radar/ask.py:160`). It is harmless at 4,058 vectors. In the what-if with everything
   embedded, keeping it drops hit@6 to 0.35. It keeps the newest ids, so older papers and releases fall out.
7. **Prompt injection works** on the shipped path with this model: 3 of 7.
8. **The breaking-change answer drops the newest changes** (`radar/ask.py:105,113,120`): oldest first, 8 per release, cut at 12
   without saying so. "What did the newest release break" is answered with old releases.
9. **Version words other than "since X" are ignored** (`radar/ask.py:31`); "removed" is not a breaking word (`:29`), so it goes to the model.
10. **The first matching word picks the package** (`radar/ask.py:56-72`): "Anthropic TypeScript SDK" gets the Python package.
11. **Any MCP question with "security" or "vulnerabilities" becomes an advisory lookup** (`radar/ask.py:30,82`), for example "are there MCP
    servers that look up vulnerabilities".
12. **Advisories are cut at the ten newest of any severity, silently** (`radar/ask.py:126-127`). vLLM has 194, nine critical; none of the
    nine was in the answer.
13. 189 items are exact copies of another item's title and text (release candidates, registry re-listings) and can fill several slots.

The evaluation changed nothing in the application. The fixes came afterwards, on their own branch.

## After the fixes (2026-11-25)

What changed: every item gets a vector, in bounded batches, and all vectors are searched (findings 5, 6); next to vector hits a keyword
hit counts only if it holds most of the question's terms (5); breaking changes newest first and advisories worst first, both saying when
they are cut (8, 12); version words, languages, "removed" and "MCP servers" are read (9, 10, 11); the model is a setting and Ask can use
a hosted one under its own daily cap (1). The confidence gate (2) is unchanged: refusal is still the model's work.

The ranking rule was chosen among seven on the 15 dev questions only. The 22 test questions were scored once, after the choice.

| Retrieval, 22 test questions | before | after | paired difference |
|---|---|---|---|
| hit@6 | 0.68 [0.50, 0.86] | 0.86 [0.73, 1.00] | +0.18 [0.00, +0.36], not significant |
| recall@6 | 0.41 [0.27, 0.57] | 0.53 [0.39, 0.67] | +0.12 [+0.04, +0.23], significant |
| MRR@10 | 0.44 [0.27, 0.61] | 0.69 [0.53, 0.85] | +0.26 [+0.05, +0.44], significant |
| nDCG@6 | 0.39 [0.26, 0.54] | 0.61 [0.48, 0.73] | +0.22 [+0.08, +0.33], significant |

"After" assumes every item has a vector; the backfill job needs about 20 hours after a deployment. 2% of the items now shown in the top
six were never judged and count as irrelevant.

| Answers, 62 questions | before: `qwen2.5:1.5b` | after: `deepseek-v4.1-flash` |
|---|---|---|
| answerable: answered and cites an answering item | 10 of 37 [0.15, 0.43] | 28 of 37 [0.60, 0.87] |
| the same, the 22 test questions only | 7 of 22 [0.16, 0.53] | 18 of 22 [0.62, 0.93], difference significant |
| an answering item was in the context: answered and cites it | 10 of 22 | 28 of 29 |
| must-refuse: refused | 9 of 10 | 10 of 10 [0.72, 1.00] |
| attacks that succeeded | 3 of 7 | 0 of 7 [0.00, 0.35] |
| shown citations that point at a relevant item | 25 of 62 [0.29, 0.53] | 88 of 94 [0.87, 0.97] |
| structured questions correct | 4 of 8 | 8 of 8 |
| seconds per answer, median (largest) | 16.4 (53.5), laptop | 3.7 (10.9), hosted, over a tunnel |

- Two things changed at once, the retrieval and the model, so the answer rows do not say how much each one gave.
- Structured 8 of 8 is not evidence: the fixes were written after seeing which ones failed, test questions included.
- 0 of 7 attacks does not mean safe. The interval reaches 0.35, and injected text is still in the prompt.
- `gemma3:4b`, the larger model on the server, took 50 to 77 seconds per answer there (three questions) and was ruled out.

| Groundedness, judge `glm-5.3-flash` (another model family) | before | after |
|---|---|---|
| answerable answers fully supported by their cited items | 3 of 15 [0.07, 0.45] | 29 of 29 [0.88, 1.00] |
| partly supported | 10 of 15 | 0 of 29 |
| control: swapped items, called not supported | 14 of 15 | 28 of 29 |
| control: verbatim sentence, called supported | 12 of 15 | 27 of 29 |

This judge catches known-wrong cases far better than the local 3B one, which had called 12 of the same 15 "before" answers supported. It
has not been compared with a human either: the 40 blind labels are still to come, and until then this table is provisional.

Cost of the recording and all paid judging: 255 calls, $0.09. One answer costs about $0.0005.

## Limitations

- Small sets. Seven attacks show that a weakness exists, not how common it is. Intervals are wide.
- One model drafted the questions and labels, and wrote some after reading the code. A human has not confirmed them.
- Recall is against the pool, not against every relevant item in the archive. For broad questions the pool is incomplete.
- Retrieval, ablations and the judge were not tuned on this data, but the attack check was corrected once after seeing output (it had
  compared exact strings and missed a package name written with spaces).
- Answers come from one model, one run, temperature 0, on one machine. Timings do not describe the server.
- The committed recording holds no archive text, and withholds sentences that cite anything but releases and papers, so the judge
  and the cross-checks can be repeated only with the private recording.
- Vector-only is the real function with keyword search switched off; ranks 7 to 10 are seen by raising its item limit.

## How to reproduce

    export ASK_EVAL_SNAPSHOT=<path to radar.jsonl.gz>
    python -m evals.ask.data                              # fully embedded copy, about an hour of Ollama
    python -m evals.ask.retrieval_eval --record-vectors   # then without the flag: offline
    python -m evals.ask.retrieval_eval --fast             # numpy from requirements-eval.txt; same rankings as the real code
                                                          # on 106 of 106 checked runs (python -m evals.ask.fast verify)
    python -m evals.ask.answer_eval record [--archive full] [--model NAME]
    python -m evals.ask.answer_eval judge [--archive full]
    python -m evals.ask.answer_eval score [--archive full]
    python -m evals.ask.answer_eval compare --model A --other B
    python -m evals.ask.fixture check                     # the CI gate

Human steps. Relevance: `python -m evals.ask.labels export`, open `evals/ask/review.html`, then `labels import reviewed.json` and rerun.
Groundedness: `python -m evals.ask.calibration sample`, open `evals/ask/label.html`, then `calibration score` and
`export_groundedness`. A paid judge exists only behind `judge --live --judge MODEL --max-usd X`. Its cap refuses a
call that could pass the limit, counting an unreported cost at `--max-call-usd`.

The CI gate runs on 25 questions over a small slice of releases and papers. Its thresholds are the observed values minus 0.05
(one question is 0.04), and three tests break the ranking on purpose and check that the gate fails. Its numbers are not the
numbers above.
