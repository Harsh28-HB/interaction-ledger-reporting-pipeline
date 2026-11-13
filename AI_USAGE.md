# AI Usage Disclosure

## What I used AI for

AI assistance supported the Assignment A audit and implementation, including:

- Reviewing the supplied schemas and requirements against ingestion,
  normalization, event-linking, and report behavior.
- Drafting and refining the SQLite ingestion, encryption, and reporting code.
- Adding focused tests for invalid input, merging, PII handling, event
  idempotency, orphan relinking, report output, and transaction rollback.
- Updating the run instructions and submission documentation.

## What I wrote or decided myself

I chose Assignment A, ran the supplied generator, reviewed test and report
outputs, and made the final decisions about data handling and validation.
Generated code and suggested fixes were checked against the supplied
requirements and executed locally.

## Where AI was wrong or unhelpful

Some early suggestions did not match the repository structure: ingestion
functions were initially placed in the wrong module, and an import command
failed because the expected function was absent. Execution exposed these
problems, and the implementation was corrected before the final validation.

## How I verified AI-assisted work

- All 61 Assignment A tests passed. They use temporary databases and
  test-only keys.
- Both drops from an isolated seed-2026 small-data run were ingested, and all
  five reports were generated.
- Re-ingesting both drops left the database contents and report bytes
  unchanged.
- The five reports decoded as UTF-8 without a BOM.
- A scan of stored database text and generated reports found no decrypted
  lead phone numbers.

The seed-2026 validation ledger, generated input, and reports were kept under
the system temporary directory, separate from the existing Assignment A
ledger and report files.
