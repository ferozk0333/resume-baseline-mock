# Backlog

Agreed but not scheduled. Each item names where it came from.

- **Undo for approved name merges** (specs/15 review). Approved merges can only be changed by editing
  `config/market_synonyms.json`. An Undo on the Admin → Market baselines merges list would remove the mapping and
  put the suggestion back to pending.
- **Admin list of "unknown"-level postings with a manual level override** (specs/15 review). Postings whose level
  the code and the extraction couldn't state count toward no level baseline. A filtered list with a level picker
  would let an admin set it (stored with level_method = "manual").
