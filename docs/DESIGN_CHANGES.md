# Design Changes

This file lists each place the built prototype differs from the Assignment 2 Solution Design Report. For each one it gives the reason and the effect. Section references are to that report.

| # | Planned | Built | Why | Effect |
|:--|:--|:--|:--|:--|
| 1 | NFR-5: a notice "whose codes were largely uninterpreted" goes to the manager. No number was given. | More than 50% of the letters and digits in the code text not covered by any extracted value. Label words such as "Lot" and "Best by" are left out of the count. | The Assignment 2 feedback asked what "largely uninterpreted" means. | The threshold can be tested. 0 of the 23 sample notices cross it. |
| 2 | Section 7.4: the matcher's Tier 3 compares descriptions only when the notice has no product code. | Tier 3 also runs when the invoice line has no product code that can be compared. | Many invoices print no UPC. Under the planned rule, such a line could never match anything, and a missed product stays on the shelf. | More lines reach a person for review. They are marked lower certainty. |
| 3 | Not covered. | A line whose product code matches, but whose printed lot or best-by date falls outside the notice, is listed under "Compared and set aside" with the reason. | A silent drop would hide a near miss from the reviewer. | The reviewer sees why a line with the right code is not a candidate. |
| 4 | Section 7.4: a Tier 2 candidate is "proposed as needs-check-on-shelf". | The matcher records the suggestion, but the candidate stays Pending until a named person decides. | FR-5: no status changes without a recorded human decision. | The suggestion is shown on the review screen as a suggestion only. |
| 5 | Section 7.5: only confirmed candidates reach the worklist. | A needs-check-on-shelf decision also produces a worklist line, of the kind "Check on shelf". The line tells the person at the shelf to read the lot and date on each unit and pull only units that match. | The Assignment 2 feedback found that needs-check-on-shelf candidates had no path into physical checking. | Every decision except Reject ends on the floor with a named outcome. |
| 6 | Section 7.4: a worklist line closes as "found and pulled" or "not found". | Outcomes are Found and pulled, None found, and Could not verify. Could not verify routes the notice to the manager. | A torn or unreadable label is a real case. Recording it as "not found" would be wrong. | No line can close by implying the shelf is clear. |
| 7 | Section 7.3: parts call each other over HTTPS. | One Python program and one local SQLite file. The parts call each other as functions, and the review screen is a web page served only on this computer (127.0.0.1). | The prototype runs on one computer for one store, so network calls between its own parts would add failure points without adding a control. | Setup needs only Python. The interface contracts are the database tables in `recall_check/db.py`. |
| 8 | Section 7.4 case statuses: Registered, In review, Worklist issued, Complete, Stale. | Added Extracted, Routed to manager and No candidate identified. | Each of these is a real state a notice reaches, and NFR-3 asks that nothing sit in an undefined state. | Every notice shows exactly one status. |
| 9 | FR-8: the worklist line gives "the quantity last received". | The line shows the delivery that matched and, if there is a later one, the most recent delivery of the same item. | The matched delivery and the latest delivery can differ, and the person at the shelf needs both. | — |
| 10 | Not covered. | Loading the purchase file requires a named person. The same file cannot be loaded twice, which is checked by its fingerprint. | Loading a file twice would create duplicate candidates. | — |

## Not built yet

These are planned and not yet in the prototype:

- **E7 coordinator rules.** Completion only by a named person with every line closed. Staleness when a recall is expanded. Class I deadlines.
- **E10 access gate.** Until it is built, the review screen trusts the name a person types.
- **Offline worklist (NFR-13).**
- **Encryption at rest (NFR-8).**
- **FR-9, the purchasing hold and returns flag.** This is out of the prototype by design.

## Known limitation added by the build

- A decision on a candidate is recorded once and cannot be corrected in the prototype.
