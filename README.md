# Recall Shelf-Check

AI-assisted recall notice matching for store-level shelf checks. CEN 4010 Final Project, Cody Graham.

> Work in progress. Sections are filled in as the prototype is built.

## Purpose
When a store receives a food recall notice, nothing connects the notice to what is on its shelves. This prototype reads the product codes, lot codes and dates from a recall notice, matches them against the store's purchase records, and gives a named person a shelf-check worklist to confirm. It never decides that a product is safe, never tells a store it is clear, and contacts no one outside the store.

## Architecture
Built on the Document Intelligence pipeline from the CEN 4010 Guide to Canonical Architectures. The full design is in the Solution Design Report.

| Element | Code | Status |
|:--|:--|:--|
| E1 Notice intake | `recall_check/intake.py` | Built |
| E2 Notice store | `recall_check/db.py` | Built |
| E3 Code extraction, the only AI part | `recall_check/extract.py` | Built |
| E4 Purchase record store | `recall_check/purchases.py` | Built |
| E5 Rule-based matcher, tiers 1 to 3, no AI | `recall_check/matcher.py` | Built |
| E6 Candidate and review store | `recall_check/review.py` | Built |
| E8 Review workbench, local web page | `recall_check/web.py` | Built |
| E9 Worklist composer | `recall_check/worklist.py` | Built |
| E11 Audit log, append-only | `recall_check/audit.py` | Built |
| E7 Coordinator rules, E10 Access gate | | Not built yet |

Where the build differs from the design, the change and its reason are recorded in [docs/DESIGN_CHANGES.md](docs/DESIGN_CHANGES.md).

## Setup
Requires Python 3.10 or newer. No packages to install.

```
git clone https://github.com/ccgcomp/recall-check.git
cd recall-check
```

## Configuration
Code extraction runs in one of two modes:

- `replay`, the default. Replays answers Claude gave earlier for the sample notices, saved in `data/extractions`. Needs no Claude account.
- `claude`. Calls Claude through the Claude Code command line using your own Claude login. Requires [Claude Code](https://code.claude.com/docs/en/setup) and a paid Claude plan. No API key is used.

## Run
```
python -m recall_check fetch-samples     # download sample notices from openFDA
python -m recall_check register          # register them (FR-1)
python -m recall_check extract           # read their codes, replay mode
python -m recall_check show F-0865-2017  # show one notice and its codes
python -m recall_check import-purchases --by "Your Name"   # load the sample purchase file
python -m recall_check match             # match notices against purchases (FR-4)
python -m recall_check review            # open http://127.0.0.1:8000 to review
python -m recall_check audit             # print the audit log
```

The review page shows each candidate beside the notice text it came from. Confirm, reject or mark a candidate needs-check-on-shelf with your name, then record what the shelf held on the worklist page. The same steps work from the command line with `candidates`, `decide`, `worklist` and `outcome`; run `python -m recall_check <command> --help` for each.

To start over, delete `data/local/recall.db`.

## Test
```
python -m unittest -v
```

## Data
Recall notices are public records from the [openFDA food enforcement endpoint](https://open.fda.gov/apis/food/enforcement/). Store purchase records in `data/store/purchases_sample.csv` are made up. Some rows are written to match the real notices, some are near misses, and two are deliberately invalid to show rejected rows. Made-up product codes start with 2. GS1 reserves that prefix for restricted circulation numbers, which are not global product codes, so these codes do not point at a real brand's product ([GS1 summary of prefixes 02 and 20 to 29](https://www.gs1.org/docs/barcodes/SummaryOfGS1MOPrefixes20-29.pdf)). No real store data, personal data, credentials or keys are stored in this repository.

## Known limitations
To be completed.

## AI assistance
Claude helped draft the code, tests and documentation, and every draft was run and checked before it was kept. Inside the prototype, AI is used in one place only: reading codes out of a notice. [docs/AI_ASSISTANCE.md](docs/AI_ASSISTANCE.md) records what AI did, what was checked, and what was rejected or revised.

## Repository
https://github.com/ccgcomp/recall-check
