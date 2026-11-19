# ResearchLens · STEM2Connect demo

## Application description

**Track** — Scientific Research

ResearchLens helps researchers inspect how the makeup of a dataset and the choice of a target population affect a comparison. Upload a CSV or start with a synthetic example, describe the claim, and confirm an analysis plan for a binary outcome, two groups, and one stratification variable. ResearchLens shows observed rates, rates within each stratum, and rates standardized to the same user-defined mix. Each estimate links to its formula and, where applicable, the underlying counts. A downloadable experiment bundle contains the plan, results, and a Python script that reproduces the calculation. An optional local Qwen explanation references the calculated estimates. A sample composition chart exposes the imbalance, and a reweighting control runs a new version of the same experiment and shows both standardized results.

**AI disclosure.** OpenAI Codex assisted with backend and frontend implementation, tests, review and demo preparation. An optional server-side model can explain aggregate estimates with references to the underlying results. The application was verified with Qwen3-4B-Instruct-2507 running locally via Ollama.

In the example, system B has a higher observed success rate because it received a much larger share of easy tasks. A has a higher success rate within each difficulty level and under an equal-mix target. The data are synthetic, and the comparisons are descriptive.

## Extended presentation script · 4 minutes 35 seconds

| Time | Screen and action | Narration |
| --- | --- | --- |
| 0:00–0:30 | Open ResearchLens. Select **Search benchmark** and point out the synthetic data label. | “Can we tell which search system succeeds more often? In this synthetic benchmark there are 200 trials, with 100 per system. The mix of easy and hard tasks differs between systems.” |
| 0:30–1:10 | Open the preview and the confirmed plan, or use **Edit plan** to show the input controls. Use the default example claim, “System B has a higher success rate than A.” Confirm `success` with positive value `1`, group by `system`, and stratify by `difficulty`. Set the target mix to 50% easy and 50% hard. Run the confirmed plan. | “I define success, select the two systems and choose difficulty as the stratification variable. For the standardized comparison, both systems get the same target mix.” |
| 1:10–2:15 | Open the observed and within-difficulty estimates. Click a rate to expose counts and formula. Point to A’s 10 easy and 90 hard trials, and B’s 90 easy and 10 hard trials. | “The observed rates are 27% for A and 73% for B. On easy tasks, A scores 9 out of 10 and B 72 out of 90. On hard tasks, A scores 18 out of 90 and B 1 out of 10. A leads within both difficulty levels. The observed rates combine each system’s own mix.” |
| 2:15–3:00 | Open the standardized estimates and show the common weights and A’s weighted formula. | “With a 50–50 target mix, A is at 55% and B is at 45%. For A, that’s half of 90% plus half of 20%. The standardized rates describe the explicitly chosen target population. This comparison does not establish causality.” |
| 3:00–3:30 | Request an explanation with the synthetic run. Show a statement and its linked estimates if the configured service responds. If it is unavailable, show the actual error and the existing estimates. | “An explanation can point back to the numbers supporting it. The calculation and its formulas are available even when the explanation service is unavailable.” |
| 3:30–4:05 | Download the ZIP with the synthetic CSV included. Open a prepared terminal in the extracted directory and run `python3 reproduce.py`. Point to the matching 55% and 45% as proportions `0.55` and `0.45`. | “The bundle saves the plan and the results. The script checks the archived files and recomputes the estimates in this directory using Python’s standard library.” |
| 4:05–4:35 | On the upload screen, choose the checked-in `stable-search.csv` as a custom CSV. Enter B as Group 2 when it falls outside the preview. Use the same column roles and equal weights, then show its observed and standardized rates. | “The same workflow accepts a CSV. This second synthetic dataset gives 85% for A and 65% for B in both comparisons. Both groups already have the same 50–50 mix as the target, so their rates are unchanged.” |

Numeric references are the [Search benchmark fixture](../contracts/fixtures/run.json) and the [Stable comparison fixture](../contracts/fixtures/stable-run.json). The [Stable comparison CSV](../contracts/fixtures/stable-search.csv) can be used to exercise the upload path without exposing user data. The extended script is for a live presentation. A separate narrated review cut was assembled from screenshots of the working browser flow and the verified downloaded archive.

## Submission materials and status

| Material | Status |
| --- | --- |
| English project description | Draft above. Confirm it against the build used for recording. |
| Repository and setup instructions | [Repository](https://github.com/ozerinpro-arch/STEM2Connect) and [full setup instructions](../README.md). Use the integrated branch for review. |
| Reproducible experiment | Downloaded a ZIP from the recorded browser run and reproduced its estimates in a clean directory. |
| Video | A local MP4 with English synthetic narration is prepared for review. YouTube and Vimeo publishing are on hold. The [official rules](https://stem2connect-online-hackathon.devpost.com/rules) set a six-minute maximum. |
| Browser and user checks | Automated browser checks passed for the example and CSV, plan edits, session reload, keyboard, two themes, four viewport widths, model failure and ZIP reproduction. Separate trials with 3–5 people remain pending. |
| Live AI verification | Verified the live Qwen response through the browser, opened a cited formula and restored the explanation after reload. Also tested the unavailable response separately. |
| Participant details and final submission | Confirm the participant names and roles, publish approved materials, and complete submission. These actions are pending. |
