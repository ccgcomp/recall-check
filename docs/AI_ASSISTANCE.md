# AI Assistance Record

This file records where AI was used to build the prototype, what was checked, and what was rejected or changed. The Final Project asks for this record, and the Solution Design Report (Section 8.3) set the rules it follows.

## Two different uses of AI

| Use | Tool | Role |
|:--|:--|:--|
| Building the prototype | Claude (Anthropic), working in the Assignment folder through the Claude desktop app | Drafted code, tests, the extraction prompt and documentation. Every draft was run against tests and real notices before it was kept. |
| Inside the prototype | Claude, called through the Claude Code command line (`claude -p`) | Element E3 only: reads product codes, lots and dates out of a recall notice. Nothing else in the system uses AI. |

## Decisions made by the developer

These were decided by Cody Graham, not generated:

- Continuing the Assignment 1 and 2 project and its requirements.
- Python with a local web page, and no packages to install.
- Calling Claude through his own Claude login instead of an API key, with replay mode as the default so a grader needs no account.
- A public repository on his GitHub account, under the MIT license.
- Running the real extraction himself, on his own computer, for all 23 sample notices.
- Every commit and push to the repository.

The matching tiers, the candidate statuses and the rule that only a named person changes a status come from the Assignment 2 design (Section 7.4).

## Rules followed

- Expected test results are worked out by hand from the notice text and the sample purchase rows. They are never copied from the output of the code being tested.
- No real store data, personal data, passwords or keys go into the repository.
- The extraction prompt has a version (`p1`), and every extracted value records the extractor version that produced it (NFR-11).

## Outputs checked and revised

### Week 1, notice intake and extraction

1. **Claude wrapped its answer in a code fence.** The first test call returned JSON inside a ```` ```json ```` block, which a plain JSON reader rejects. The parser now removes the fence before reading.
2. **Project instructions leaked into extraction.** Run from the project folder, Claude Code also loaded the project's own instruction file, so the extraction was not shaped by the prompt alone. Extraction now runs from an empty temporary folder.
3. **The "largely uninterpreted" measure was wrong, not the AI.** On the first real run, several notices were routed to the manager as largely unread. Reading them by hand showed Claude had extracted the codes correctly. The measuring code had three faults:
   - It located a code in the product line when the same code also appeared in the code line.
   - It counted a product name repeated on every line as unread.
   - It counted label words such as "Lot" and "Best by" as unread.

   After the fix, 0 of 23 notices are routed. The highest unread share is 46%, on H-0366-2026, where "LotL355246" is printed run together. Four regression tests cover these cases.
4. **Claude's own "uninterpreted" list is not trusted.** Claude sometimes listed a value it had already extracted, such as "05/2018" or "JULY-2027", as uninterpreted. The unread share is therefore measured on the notice text itself, not taken from Claude's list.

### Week 2, matching, review and worklist

5. **Most recent delivery used the wrong comparison.** A test worked out by hand expected a later delivery of the same cookies to show as the most recent one. The code compared product codes as raw text, so "7402 3550 0110" and "740235500110" looked like different items. It now compares normalized codes.
6. **Code that only ran on newer Python.** One line of the review page used a quoting style that Python 3.12 accepts but Python 3.10 rejects. It was caught and rewritten before release. The tests now run on Python 3.10, 3.11 and 3.13.
7. **Broken markup on the worklist page.** A label on the worklist page was generated with escaped quotes, which would have broken its styling. It was found on review and fixed, and the screens were checked in a browser.
8. **An unchecked claim in the README.** A first draft said product codes starting with 2 are "reserved for in-store use." Checked against GS1's own summary of prefixes 02 and 20 to 29, the accurate wording is that they are restricted circulation numbers, not global product codes. The README was reworded and links the source.
9. **Rules the AI proposed that the developer must approve.** The Tier 3 thresholds were proposed by Claude while writing the matcher. They are: the brand plus at least half the product-name words, or every product-name word when no brand appears. Section 8.3 of the design says the matching rules are the developer's to set. Reviewed and approved unchanged by Cody Graham on 2026-11-19. The rule leans toward flagging more lines, because a missed product stays on the shelf while a wrong flag costs only a reviewer's rejection.

## Known limits of the AI step

- Extraction quality is measured with the NFR-5 spot check on real notices, not assumed.
- Replay mode reproduces the answers Claude gave on 2026-11-12. A new Claude call can give a different answer for the same notice.
- A notice whose extraction fails, or whose codes are mostly unread, goes to the manager with its original text. It is never dropped.
